#!/usr/bin/env python3
"""Agent RAG quality eval harness — Flogents Studio + rageval (rag-evaluator).

Runs a per-agent GOLDEN SET against a deployed agent's RAG chat endpoint
(/api/chat, which returns both the answer and the retrieved sourceDocuments),
ships each (query, chunks, answer) to the rageval service, then aggregates the
scores FOR THIS RUN ONLY and asserts them against per-agent thresholds.

It is pure stdlib (urllib) and touches NO Flogo code — it only calls the
existing HTTP endpoints. Exit code is non-zero if any threshold gate fails,
so it can drop straight into CI.

Quality is measured by rageval:
  - faithfulness      : % of answer sentences grounded in retrieved chunks
  - answerRelevance   : answer vs query similarity
  - contextRelevance  : retrieved chunks vs query similarity
  - hallucinationPct  : % of answers with ungrounded sentences
  - llmOverall        : LLM-as-a-judge overall (only if rageval judge enabled)

Usage:
  python3 tests/eval/eval_harness.py --goldenset tests/eval/goldenset.devops.json
  # overrides:
  python3 tests/eval/eval_harness.py -g <file> --chat-url http://localhost:7201/api/chat \
      --rageval-url http://localhost:9090 --timeout 300 --json-out /tmp/eval.json
"""
import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

GREEN, RED, YEL, DIM, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _post(url, payload, headers=None, timeout=120):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
        return r.status, (json.loads(body) if body.strip() else {})


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _basic_auth(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def run(cfg, args):
    chat_url = args.chat_url or cfg["chatUrl"]
    rageval = (args.rageval_url or cfg.get("ragevalUrl", "http://localhost:9090")).rstrip("/")
    collection = cfg["collection"]
    topk = cfg.get("topK", 5)
    auth = cfg.get("auth", {"user": "flogo", "password": "changeme"})
    thr = cfg["thresholds"]
    run_id = "eval-" + uuid.uuid4().hex[:10]
    cases = cfg["cases"]

    print(f"{DIM}run_id={run_id}  agent={cfg.get('agent')}  collection={collection}  "
          f"cases={len(cases)}{RST}")
    print(f"{DIM}chat={chat_url}  rageval={rageval}{RST}\n")

    # health check rageval
    try:
        _get(f"{rageval}/health", timeout=5)
    except Exception as e:
        print(f"{RED}rageval not reachable at {rageval}: {e}{RST}")
        return 2

    submitted = []
    # PHASE 1 — collect every answer FIRST (no eval events in flight yet), so the
    # agent's chat model never competes with rageval's LLM judge on a shared Ollama.
    collected = []
    for i, c in enumerate(cases):
        tid = f"{run_id}-{c.get('id', i)}"
        q = c["query"]
        # ask the agent's RAG chat endpoint (retry once on empty answer, since a
        # busy single-threaded local LLM can occasionally return an empty body)
        answer, chunks = "", []
        for attempt in range(2):
            try:
                _, resp = _post(
                    chat_url,
                    {"message": q, "collectionName": collection, "topK": topk,
                     "sessionId": f"{tid}-a{attempt}"},
                    headers={"Authorization": _basic_auth(auth["user"], auth["password"])},
                    timeout=args.chat_timeout,
                )
            except Exception as e:
                print(f"{RED}  ✗ chat failed for [{c.get('id', i)}]: {e}{RST}")
                resp = {}
            answer = resp.get("answer") or ""
            chunks = resp.get("sourceDocuments") or []
            if answer and chunks:
                break
            if attempt == 0:
                time.sleep(3)
        if not answer or not chunks:
            print(f"{YEL}  ⚠ [{c.get('id', i)}] empty answer/chunks "
                  f"(answer={len(answer)} chars, chunks={len(chunks)}) — skipped{RST}")
            continue
        collected.append((c, tid, q, answer, chunks))
        print(f"{DIM}  ✓ answered [{c.get('id', i)}] (answer={len(answer)} chars, "
              f"chunks={len(chunks)}){RST}")

    # PHASE 2 — ship the collected (query, chunks, answer) triples to rageval
    for c, tid, q, answer, chunks in collected:
        event = {
            "pipelineId": run_id,
            "platform": "flogo",
            "traceId": tid,
            "collection": collection,
            "query": q,
            "chunks": chunks,
            "answer": answer,
        }
        if c.get("expectedAnswer"):
            event["expectedAnswer"] = c["expectedAnswer"]
        try:
            st, _ = _post(f"{rageval}/eval/v1/events", event, timeout=30)
            submitted.append(tid)
            print(f"{DIM}  → submitted [{tid.replace(run_id + '-', '')}] http={st}{RST}")
        except Exception as e:
            print(f"{RED}  ✗ rageval submit failed for [{tid}]: {e}{RST}")

    if not submitted:
        print(f"\n{RED}No events submitted — nothing to score.{RST}")
        return 2

    # 3) poll results until this run's traceIds are all scored (judge can be slow)
    print(f"\n{DIM}waiting for async scoring of {len(submitted)} events "
          f"(timeout {args.timeout}s)…{RST}")
    want = set(submitted)
    rows_by_tid = {}
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            rows = _get(f"{rageval}/eval/v1/results?collection={collection}&limit=500")
        except Exception:
            rows = []
        for row in rows or []:
            if row.get("traceId") in want:
                rows_by_tid[row["traceId"]] = row
        if len(rows_by_tid) >= len(want):
            break
        time.sleep(4)

    rows = list(rows_by_tid.values())
    if not rows:
        print(f"{RED}No scored results returned within timeout.{RST}")
        return 2
    scored, missing = len(rows), len(want) - len(rows)

    # 4) aggregate THIS run only
    def avg(key):
        vals = [r.get(key, 0.0) for r in rows]
        return sum(vals) / len(vals) if vals else 0.0

    agg = {
        "faithfulness": avg("faithfulness"),
        "answerRelevance": avg("answerRelevance"),
        "contextRelevance": avg("contextRelevance"),
        "overall": avg("overallScore"),
        "llmOverall": avg("llmOverall"),
        "hallucinationPct": 100.0 * sum(1 for r in rows if r.get("hasFlags")) / len(rows),
    }
    judge_on = any(r.get("llmOverall", 0) > 0 for r in rows)

    # 5) per-case table
    print(f"\n{'id':18}{'faith':>7}{'ansRel':>8}{'ctxRel':>8}{'llmOv':>7}  flags")
    print("-" * 60)
    for r in sorted(rows, key=lambda x: x.get("traceId", "")):
        cid = r.get("traceId", "").replace(run_id + "-", "")
        flags = ",".join(r.get("flags") or []) if r.get("hasFlags") else ""
        print(f"{cid:18}{r.get('faithfulness',0):>7.2f}{r.get('answerRelevance',0):>8.2f}"
              f"{r.get('contextRelevance',0):>8.2f}{r.get('llmOverall',0):>7.2f}  "
              f"{RED if flags else DIM}{flags or '-'}{RST}")

    # 6) gates
    gates = [
        ("faithfulness",     agg["faithfulness"],     ">=", thr["faithfulness"]),
        ("answerRelevance",  agg["answerRelevance"],  ">=", thr["answerRelevance"]),
        ("contextRelevance", agg["contextRelevance"], ">=", thr["contextRelevance"]),
        ("hallucinationPct", agg["hallucinationPct"], "<=", thr["hallucinationPctMax"]),
    ]
    if judge_on and "llmOverallMin" in thr:
        gates.append(("llmOverall", agg["llmOverall"], ">=", thr["llmOverallMin"]))

    print(f"\n{'metric':18}{'actual':>9}{'op':>4}{'gate':>8}   result")
    print("-" * 52)
    failed = 0
    for name, actual, op, gate in gates:
        ok = actual >= gate if op == ">=" else actual <= gate
        failed += 0 if ok else 1
        tag = f"{GREEN}PASS{RST}" if ok else f"{RED}FAIL{RST}"
        print(f"{name:18}{actual:>9.2f}{op:>4}{gate:>8.2f}   {tag}")

    print(f"\n{DIM}scored {scored}/{len(want)} (missing {missing})  judge={'on' if judge_on else 'off'}{RST}")
    verdict = f"{GREEN}QUALITY GATE PASSED{RST}" if failed == 0 else f"{RED}QUALITY GATE FAILED ({failed} metric(s)){RST}"
    print(verdict)

    if args.json_out:
        out = {"runId": run_id, "agent": cfg.get("agent"), "collection": collection,
               "scored": scored, "missing": missing, "judge": judge_on,
               "aggregate": agg, "thresholds": thr,
               "gatesFailed": failed,
               "cases": [{"id": r.get("traceId", "").replace(run_id + "-", ""),
                          "faithfulness": r.get("faithfulness"),
                          "answerRelevance": r.get("answerRelevance"),
                          "contextRelevance": r.get("contextRelevance"),
                          "llmOverall": r.get("llmOverall"),
                          "flags": r.get("flags")} for r in rows]}
        with open(args.json_out, "w") as f:
            json.dump(out, f, indent=2)
        print(f"{DIM}wrote {args.json_out}{RST}")

    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description="Agent RAG quality eval harness (rageval)")
    ap.add_argument("-g", "--goldenset", default="tests/eval/goldenset.devops.json")
    ap.add_argument("--chat-url", default=None, help="override agent /api/chat URL")
    ap.add_argument("--rageval-url", default=None, help="override rageval base URL")
    ap.add_argument("--timeout", type=int, default=300, help="max seconds to wait for scoring")
    ap.add_argument("--chat-timeout", type=int, default=120, help="per-question chat timeout")
    ap.add_argument("--json-out", default=None, help="write a JSON report to this path")
    args = ap.parse_args()

    try:
        cfg = json.load(open(args.goldenset))
    except Exception as e:
        print(f"{RED}cannot read goldenset {args.goldenset}: {e}{RST}")
        return 2
    return run(cfg, args)


if __name__ == "__main__":
    sys.exit(main())

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Save, Play, Square, Download, Sparkles, MessageSquare, Container, CircleStop, RefreshCw, Monitor, ExternalLink, CheckCircle, XCircle } from "lucide-react";
import ThemeToggle from "../components/ThemeToggle";
import ActivationModeModal from "../components/ActivationModeModal";
import {
  createAgent,
  deployAgent,
  dockerBuildImages,
  dockerDeploy,
  dockerDeployStatus,
  dockerDeployStop,
  exportDockerCompose,
  exportKubernetes,
  generateAgentConfig,
  getAgent,
  getAgentFeedback,
  getAgentRuntime,
  getDeployStatus,
  getIngestionHealth,
  improveAgentConfig,
  ingestDocuments,
  ingestFile,
  ingestGitHub,
  ingestUrl,
  restartIngestion,
  undeployAgent,
  updateAgent,
} from "../api";
import type { Agent, AgentConfig, AgentRuntime, DockerDeployResult, DockerDeployStatus, FeedbackRecord, GeneratedConfig } from "../types";

const PROVIDER_OPTIONS = ["Ollama", "OpenAI", "Anthropic", "Groq", "Custom"];

const CHUNK_STRATEGY_OPTIONS = [
  { value: "sentence", label: "Sentence — Best for Q&A / RAG (recommended)" },
  { value: "paragraph", label: "Paragraph — Dense prose documents" },
  { value: "heading", label: "Heading — Structured docs / wikis" },
  { value: "fixed", label: "Fixed — Unstructured / fixed-size chunks" },
  { value: "none", label: "None — Pre-chunked content" },
];

const PROVIDER_MODEL_HINTS: Record<string, string> = {
  Ollama: "llama3.2:3b · llama3.1:8b · deepseek-r1:latest",
  OpenAI: "gpt-4o · gpt-4o-mini · gpt-3.5-turbo",
  Anthropic: "claude-opus-4-7 · claude-sonnet-4-6 · claude-haiku-4-5-20251001",
  Groq: "llama-3.3-70b-versatile",
  Custom: "depends on your provider",
};

interface FormState {
  name: string;
  description: string;
  systemPrompt: string;
  collectionName: string;
  topK: string;
  chunkStrategy: string;
  embeddingProvider: string;
  embeddingModel: string;
  embeddingBaseUrl: string;
  llmProvider: string;
  llmModel: string;
  llmBaseUrl: string;
  temperature: string;
}

const DEFAULTS: FormState = {
  name: "",
  description: "",
  systemPrompt: "",
  collectionName: "",
  topK: "5",
  chunkStrategy: "sentence",
  embeddingProvider: "Ollama",
  embeddingModel: "nomic-embed-text",
  embeddingBaseUrl: "http://localhost:11434",
  llmProvider: "Ollama",
  llmModel: "",
  llmBaseUrl: "http://localhost:11434",
  temperature: "0.7",
};

// Weaviate collection names must be PascalCase, letters+digits only.
// Derive a stable unique name from the agent UUID: Agent + first 8 hex chars.
function deriveCollectionName(agentId: string): string {
  const hex = agentId.replace(/-/g, "").slice(0, 8);
  return `Agent${hex.charAt(0).toUpperCase()}${hex.slice(1)}`;
}

function toFormState(agent: Agent): FormState {
  const c = agent.config ?? {};
  return {
    name: agent.name,
    description: agent.description,
    systemPrompt: c.systemPrompt ?? "",
    collectionName: c.collectionName || deriveCollectionName(agent.id),
    topK: String(c.topK ?? 5),
    chunkStrategy: c.chunkStrategy ?? "sentence",
    embeddingProvider: c.embeddingProvider ?? "Ollama",
    embeddingModel: c.embeddingModel ?? "nomic-embed-text",
    embeddingBaseUrl: c.embeddingBaseUrl ?? "http://localhost:11434",
    llmProvider: c.llmProvider ?? "Ollama",
    llmModel: c.llmModel ?? "",
    llmBaseUrl: c.llmBaseUrl ?? "http://localhost:11434",
    temperature: String(c.temperature ?? 0.7),
  };
}

function toConfig(form: FormState): AgentConfig {
  return {
    systemPrompt: form.systemPrompt || undefined,
    collectionName: form.collectionName || undefined,
    topK: form.topK ? Number(form.topK) : undefined,
    chunkStrategy: form.chunkStrategy || undefined,
    embeddingProvider: form.embeddingProvider || undefined,
    embeddingModel: form.embeddingModel || undefined,
    embeddingBaseUrl: form.embeddingBaseUrl || undefined,
    llmProvider: form.llmProvider || undefined,
    llmModel: form.llmModel ?? undefined,
    llmBaseUrl: form.llmBaseUrl || undefined,
    temperature: form.temperature ? Number(form.temperature) : undefined,
  };
}

function applyGenerated(form: FormState, gen: GeneratedConfig): FormState {
  const c = gen.config ?? {};
  return {
    ...form,
    name: gen.name ?? form.name,
    description: gen.description ?? form.description,
    systemPrompt: c.systemPrompt ?? form.systemPrompt,
    collectionName: c.collectionName ?? form.collectionName,
    topK: c.topK != null ? String(c.topK) : form.topK,
    chunkStrategy: c.chunkStrategy ?? form.chunkStrategy,
    embeddingProvider: c.embeddingProvider ?? form.embeddingProvider,
    embeddingModel: c.embeddingModel ?? form.embeddingModel,
    embeddingBaseUrl: c.embeddingBaseUrl ?? form.embeddingBaseUrl,
    llmProvider: c.llmProvider ?? form.llmProvider,
    llmModel: c.llmModel ?? form.llmModel,
    llmBaseUrl: c.llmBaseUrl ?? form.llmBaseUrl,
    temperature: c.temperature != null ? String(c.temperature) : form.temperature,
  };
}


// ── Export modal ──────────────────────────────────────────────────────────────

function ExportModal({ title, content, onClose }: { title: string; content: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-zinc-900 rounded-xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-700">
          <h3 className="font-semibold text-zinc-100">{title}</h3>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-200 text-xl leading-none">&times;</button>
        </div>
        <pre className="flex-1 overflow-auto p-5 text-xs font-mono bg-zinc-800 rounded-b-xl whitespace-pre-wrap text-zinc-300">{content}</pre>
      </div>
    </div>
  );
}

// ── Ingest Panel ─────────────────────────────────────────────────────────────

type IngestSubTab = "text" | "file" | "url" | "github" | "api";

function IngestPanel({ collection, chunkStrategy, embeddingModel, embeddingProvider, embeddingBaseUrl, ingestionUrl, agentId, onApplyRestart }: {
  collection: string;
  chunkStrategy: string;
  embeddingModel: string;
  embeddingProvider: string;
  embeddingBaseUrl: string;
  ingestionUrl?: string;
  agentId?: string;
  onApplyRestart?: () => Promise<void>;
}) {
  const [tab, setTab] = useState<IngestSubTab>("text");
  const [pasteText, setPasteText] = useState("");
  const [pasteSource, setPasteSource] = useState("");
  const [urlValue, setUrlValue] = useState("");
  const [ghOwner, setGhOwner] = useState("");
  const [ghRepo, setGhRepo] = useState("");
  const [ghPath, setGhPath] = useState("");
  const [ghBranch, setGhBranch] = useState("main");
  const [fileList, setFileList] = useState<File[]>([]);

  // ── Ingestion service health + config drift ──────────────────────────────
  const { data: healthData, isError: healthError, error: healthErrorObj, isFetching: healthFetching, refetch: refetchHealth } = useQuery({
    queryKey: ["ingestion-health", agentId],
    queryFn: () => getIngestionHealth(agentId!),
    enabled: !!agentId,
    refetchInterval: 5_000,          // poll every 5 s so status updates quickly after service starts
    retry: 2,                         // retry twice on transient failures before marking error
    retryDelay: 1500,
    staleTime: 4_000,
  });

  const restartMut = useMutation({
    mutationFn: async () => {
      if (onApplyRestart) {
        // Save current form config first so the backend restarts with the
        // latest settings, not the stale DB values.
        await onApplyRestart();
      } else {
        await restartIngestion(agentId!);
      }
    },
    onSuccess: () => {
      // Give the service ~4 s to bind the port then re-check health
      setTimeout(() => refetchHealth(), 4000);
    },
  });

  // serviceHealthy: getIngestionHealth never throws — it returns {healthy:false} on any network error.
  // So we only rely on healthData; while loading (no data yet) we stay optimistic.
  const serviceHealthy: boolean = !agentId || (healthData?.healthy ?? true);

  // Drift: service is running but its baked-in config differs from agent config
  const configuredWith = healthData?.configuredWith;
  const hasDrift = !!(healthData?.healthy && configuredWith && (
    (chunkStrategy && configuredWith.chunkStrategy !== chunkStrategy) ||
    (embeddingModel && configuredWith.embeddingModel !== embeddingModel)
  ));

  const readFileAsText = (f: File): Promise<string> =>
    new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result as string);
      r.onerror = () => rej(r.error);
      r.readAsText(f);
    });

  const isBinaryFile = (f: File) =>
    /\.(pdf|docx|doc)$/i.test(f.name);

  const fileMut = useMutation({
    mutationFn: async () => {
      // Binary files (PDF, DOCX) must be sent as multipart so the server can
      // extract text using the built-in document parser (ledongthuc/pdf etc.).
      // Plain-text files are read client-side and sent as JSON documents.
      const binaryFiles = fileList.filter(isBinaryFile);
      const textFiles = fileList.filter((f) => !isBinaryFile(f));

      const promises: Promise<unknown>[] = [];

      if (textFiles.length > 0) {
        const docs = await Promise.all(
          textFiles.map(async (f) => ({ text: await readFileAsText(f), source: f.name }))
        );
        promises.push(ingestDocuments(collection, docs, chunkStrategy || undefined, ingestionUrl));
      }

      for (const f of binaryFiles) {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("filename", f.name);
        fd.append("collectionName", collection);
        if (chunkStrategy) fd.append("chunkStrategy", chunkStrategy);
        promises.push(ingestFile(collection, fd, ingestionUrl));
      }

      await Promise.all(promises);
    },
    onSuccess: () => setFileList([]),
  });

  const textMut = useMutation({
    mutationFn: () =>
      ingestDocuments(
        collection,
        [{ text: pasteText.trim(), source: pasteSource.trim() || "pasted-text.txt" }],
        chunkStrategy || undefined,
        ingestionUrl,
      ),
  });

  const urlMut = useMutation({
    mutationFn: () => ingestUrl(collection, urlValue.trim(), chunkStrategy || undefined, ingestionUrl),
  });

  const githubMut = useMutation({
    mutationFn: () =>
      ingestGitHub(
        collection,
        ghOwner.trim(),
        ghRepo.trim(),
        ghPath.trim(),
        ghBranch.trim() || "main",
        chunkStrategy || undefined,
        ingestionUrl,
      ),
  });

  const isPending = textMut.isPending || urlMut.isPending || githubMut.isPending || fileMut.isPending;
  const result = textMut.data ?? urlMut.data ?? githubMut.data ?? fileMut.data ?? null;
  const mutError = textMut.error ?? urlMut.error ?? githubMut.error ?? fileMut.error ?? null;

  const allDisabled = !serviceHealthy || isPending;

  const INGEST_HOST = "http://localhost:7002";
  const AUTH_HEADER = "Basic ZmxvZ286Y2hhbmdlbWU=";
  const coll = collection || "MyCollection";
  const strategy = chunkStrategy || "sentence";

  const TABS: { id: IngestSubTab; label: string }[] = [
    { id: "text", label: "Paste Text" },
    { id: "file", label: "Upload File" },
    { id: "url", label: "URL" },
    { id: "github", label: "GitHub" },
    { id: "api", label: "API Reference" },
  ];

  const API_EXAMPLES = [
    {
      label: "Paste text content",
      endpoint: "/api/ingest",
      body: `{
  "collection": "${coll}",
  "chunkStrategy": "${strategy}",
  "documents": [
    { "text": "Your document content…", "source": "doc.txt" }
  ]
}`,
    },
    {
      label: "Fetch a public URL",
      endpoint: "/api/ingest/url",
      body: `{
  "collection": "${coll}",
  "url": "https://your-docs-site.com/page",
  "chunkStrategy": "${strategy}"
}`,
    },
    {
      label: "Ingest a GitHub repo path",
      endpoint: "/api/ingest/github",
      body: `{
  "collection": "${coll}",
  "owner": "my-org",
  "repo": "my-docs",
  "path": "docs/",
  "branch": "main"
}`,
    },
    {
      label: "Ingest a Confluence space",
      endpoint: "/api/ingest/confluence",
      body: `{
  "collection": "${coll}",
  "spaceKey": "DOC",
  "baseUrl": "https://mycompany.atlassian.net/wiki"
}`,
    },
  ];

  return (
    <section className="bg-zinc-900 rounded-xl border border-zinc-700 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-100">Ingest Documents</h3>
        <div className="flex items-center gap-2">
          {/* Health badge */}
          {agentId && (
            <span className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${!healthData ? "bg-zinc-800 text-zinc-500"
              : healthData.healthy ? "bg-green-950 text-green-400 border border-green-800"
                : "bg-red-950 text-red-400 border border-red-800"
              }`}>
              <span className={`w-1.5 h-1.5 rounded-full ${!healthData ? "bg-zinc-600 animate-pulse"
                : healthData.healthy ? "bg-green-500"
                  : "bg-red-500 animate-pulse"
                }`} />
              {!healthData
                ? "checking\u2026"
                : healthData.healthy
                  ? "service online"
                  : "service offline"}
            </span>
          )}
          <span
            className={`text-xs px-2 py-0.5 rounded font-mono ${collection
              ? "bg-green-950 text-green-400 border border-green-800"
              : "bg-amber-950 text-amber-400 border border-amber-800"
              }`}
          >
            {collection || "no collection set"}
          </span>
        </div>
      </div>

      {/* Service offline banner */}
      {agentId && healthData && !healthData.healthy && (
        <div className="flex items-start gap-3 text-xs text-red-400 bg-red-950 border border-red-900 rounded-lg p-3">
          <svg className="w-4 h-4 shrink-0 mt-0.5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div className="flex-1">
            <p className="font-medium">Ingestion service is offline</p>
            <p className="text-red-600 mt-0.5">Port {healthData.port} is not responding. Start the service or click Restart.</p>
          </div>
          <button
            onClick={() => restartMut.mutate()}
            disabled={restartMut.isPending}
            className="shrink-0 flex items-center gap-1 bg-red-600 hover:bg-red-700 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
          >
            {restartMut.isPending
              ? <><svg className="animate-spin w-3 h-3" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" /></svg> Starting…</>
              : <><RefreshCw size={12} /> Start Service</>
            }
          </button>
        </div>
      )}

      {/* Config drift banner */}
      {hasDrift && (
        <div className="flex items-start gap-3 text-xs text-amber-400 bg-amber-950 border border-amber-900 rounded-lg p-3">
          <svg className="w-4 h-4 shrink-0 mt-0.5 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div className="flex-1">
            <p className="font-medium">Service config is out of sync</p>
            <p className="text-amber-600 mt-0.5">
              Running with{" "}
              {configuredWith!.chunkStrategy !== chunkStrategy && (
                <><code className="font-mono bg-amber-900/50 px-1 rounded">strategy={configuredWith!.chunkStrategy}</code> (agent: <code className="font-mono bg-amber-900/50 px-1 rounded">{chunkStrategy}</code>){" "}</>
              )}
              {configuredWith!.embeddingModel !== embeddingModel && (
                <><code className="font-mono bg-amber-900/50 px-1 rounded">model={configuredWith!.embeddingModel}</code> (agent: <code className="font-mono bg-amber-900/50 px-1 rounded">{embeddingModel}</code>)</>
              )}
              . Newly ingested documents will use the <strong>old</strong> settings until you apply.
            </p>
          </div>
          <button
            onClick={() => restartMut.mutate()}
            disabled={restartMut.isPending}
            className="shrink-0 flex items-center gap-1 bg-amber-500 hover:bg-amber-600 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
          >
            {restartMut.isPending
              ? <><svg className="animate-spin w-3 h-3" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" /></svg> Applying…</>
              : <><RefreshCw size={12} /> Apply &amp; Restart</>
            }
          </button>
        </div>
      )}

      {restartMut.isError && (
        <p className="text-xs text-red-500">{String((restartMut.error as Error)?.message ?? restartMut.error)}</p>
      )}

      {!collection && (
        <div className="text-xs text-amber-400 bg-amber-950 border border-amber-900 rounded-lg p-3">
          Set a collection name in <strong>Retrieval</strong> above and save the agent before ingesting documents.
        </div>
      )}

      {/* Sub-tab bar */}
      <div className="flex gap-0 border-b border-zinc-700">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors -mb-px ${tab === id
              ? "border-brand-500 text-brand-600"
              : "border-transparent text-zinc-500 hover:text-zinc-300 hover:border-zinc-600"
              }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Paste Text */}
      {tab === "text" && (
        <div className="space-y-3">
          <Field label="Document Content">
            <textarea
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              rows={6}
              placeholder="Paste your document text here. It will be chunked and embedded into the vector store."
              className="input resize-none text-sm w-full font-mono"
            />
          </Field>
          <Field label="Source Name">
            <input
              value={pasteSource}
              onChange={(e) => setPasteSource(e.target.value)}
              placeholder="e.g. product-faq.txt"
              className="input"
            />
          </Field>
          <div className="flex items-center gap-3">
            <button
              onClick={() => textMut.mutate()}
              disabled={!pasteText.trim() || !collection || allDisabled}
              className="flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
            >
              {textMut.isPending && (
                <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              )}
              {textMut.isPending ? "Ingesting…" : "Ingest Document"}
            </button>
            {textMut.isSuccess && <span className="text-xs text-green-600 font-medium">✓ Ingested successfully</span>}
          </div>
          {textMut.isError && (
            <p className="text-xs text-red-500 mt-1">{String((textMut.error as Error)?.message ?? textMut.error)}</p>
          )}
        </div>
      )}

      {/* File Upload */}
      {tab === "file" && (
        <div className="space-y-3">
          <div
            className="relative flex flex-col items-center justify-center gap-2 border-2 border-dashed border-zinc-700 rounded-xl p-6 text-center hover:border-brand-400 transition-colors cursor-pointer"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const dropped = Array.from(e.dataTransfer.files);
              setFileList((prev) => [...prev, ...dropped]);
            }}
            onClick={() => document.getElementById("forge-file-input")?.click()}
          >
            <svg className="w-8 h-8 text-zinc-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
            </svg>
            <p className="text-sm text-zinc-400">Drag & drop files here, or <span className="text-brand-400 font-medium">browse</span></p>
            <p className="text-xs text-zinc-600">.txt · .md · .json · .yaml · .csv · .html · .xml · .pdf · .docx</p>
            <input
              id="forge-file-input"
              type="file"
              multiple
              accept=".txt,.md,.json,.yaml,.yml,.csv,.html,.htm,.xml,.log,.flogo,.pdf,.docx"
              className="sr-only"
              onChange={(e) => {
                const selected = Array.from(e.target.files ?? []);
                setFileList((prev) => [...prev, ...selected]);
                e.target.value = "";
              }}
            />
          </div>
          {fileList.length > 0 && (
            <ul className="space-y-1">
              {fileList.map((f, i) => (
                <li key={i} className="flex items-center justify-between text-xs bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5">
                  <span className="font-mono text-zinc-300 truncate">{f.name}</span>
                  <span className="text-zinc-500 ml-3 shrink-0">{(f.size / 1024).toFixed(1)} KB</span>
                  <button
                    onClick={() => setFileList((prev) => prev.filter((_, j) => j !== i))}
                    className="ml-3 text-zinc-500 hover:text-red-400 shrink-0"
                  >✕</button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-3">
            <button
              onClick={() => fileMut.mutate()}
              disabled={fileList.length === 0 || !collection || allDisabled}
              className="flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
            >
              {fileMut.isPending && (
                <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              )}
              {fileMut.isPending
                ? `Ingesting ${fileList.length} file${fileList.length > 1 ? "s" : ""}…`
                : `Ingest ${fileList.length || ""} File${fileList.length !== 1 ? "s" : ""}`}
            </button>
            {fileMut.isSuccess && (
              <span className="text-xs text-green-600 font-medium">✓ Ingested successfully</span>
            )}
          </div>
          {fileMut.isError && (
            <p className="text-xs text-red-500 mt-1">{String((fileMut.error as Error)?.message ?? fileMut.error)}</p>
          )}
        </div>
      )}

      {/* URL */}
      {tab === "url" && (
        <div className="space-y-3">
          <Field label="URL">
            <input
              value={urlValue}
              onChange={(e) => setUrlValue(e.target.value)}
              placeholder="https://docs.example.com/page"
              className="input"
            />
            <p className="text-xs text-zinc-500 mt-1">
              The page will be fetched via HTTP GET and its content split into chunks.
            </p>
          </Field>
          <div className="flex items-center gap-3">
            <button
              onClick={() => urlMut.mutate()}
              disabled={!urlValue.trim() || !collection || allDisabled}
              className="flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
            >
              {urlMut.isPending && (
                <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              )}
              {urlMut.isPending ? "Fetching & ingesting…" : "Fetch & Ingest"}
            </button>
            {urlMut.isSuccess && <span className="text-xs text-green-600 font-medium">✓ Ingested successfully</span>}
          </div>
          {urlMut.isError && (
            <p className="text-xs text-red-500 mt-1">{String((urlMut.error as Error)?.message ?? urlMut.error)}</p>
          )}
        </div>
      )}

      {/* GitHub */}
      {tab === "github" && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Owner">
              <input value={ghOwner} onChange={(e) => setGhOwner(e.target.value)} placeholder="my-org" className="input" />
            </Field>
            <Field label="Repository">
              <input value={ghRepo} onChange={(e) => setGhRepo(e.target.value)} placeholder="my-docs" className="input" />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Path">
              <input value={ghPath} onChange={(e) => setGhPath(e.target.value)} placeholder="docs/ or README.md" className="input" />
            </Field>
            <Field label="Branch">
              <input value={ghBranch} onChange={(e) => setGhBranch(e.target.value)} placeholder="main" className="input" />
            </Field>
          </div>
          <p className="text-xs text-zinc-500">Recursively ingests all Markdown and text files at the given path.</p>
          <div className="flex items-center gap-3">
            <button
              onClick={() => githubMut.mutate()}
              disabled={!ghOwner || !ghRepo || !ghPath || !collection || allDisabled}
              className="flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
            >
              {githubMut.isPending && (
                <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              )}
              {githubMut.isPending ? "Ingesting from GitHub…" : "Ingest from GitHub"}
            </button>
            {githubMut.isSuccess && <span className="text-xs text-green-600 font-medium">✓ Ingested successfully</span>}
          </div>
          {githubMut.isError && (
            <p className="text-xs text-red-500 mt-1">{String((githubMut.error as Error)?.message ?? githubMut.error)}</p>
          )}
        </div>
      )}

      {/* API Reference */}
      {tab === "api" && (
        <div className="space-y-5">
          <p className="text-xs text-zinc-400">
            Call these endpoints directly from scripts, CI/CD pipelines, or external tools.
            The ingestion service runs on{" "}
            <code className="font-mono bg-zinc-800 px-1 rounded">http://localhost:7002</code>.
          </p>
          {API_EXAMPLES.map(({ label, endpoint, body }) => (
            <div key={endpoint}>
              <div className="flex items-baseline gap-2 mb-1.5">
                <code className="text-xs font-semibold text-zinc-300">POST {endpoint}</code>
                <span className="text-xs text-zinc-500">— {label}</span>
              </div>
              <pre className="bg-gray-900 text-green-300 rounded-lg p-3 overflow-x-auto font-mono text-[11px] leading-relaxed whitespace-pre">{`curl -X POST ${INGEST_HOST}${endpoint} \\\n  -H "Authorization: ${AUTH_HEADER}" \\\n  -H "Content-Type: application/json" \\\n  -d '${body}'`}</pre>
            </div>
          ))}
        </div>
      )}

      {/* Result / error feedback */}
      {result && (
        <div className="text-xs text-green-400 bg-green-950 border border-green-800 rounded-lg p-3 font-mono whitespace-pre-wrap">
          {result}
        </div>
      )}
      {mutError && (
        <div className="text-xs text-red-400 bg-red-950 border border-red-900 rounded-lg p-3">
          {String(mutError)}
        </div>
      )}
    </section>
  );
}

// ── Runtime health panel (Deploy tab) ────────────────────────────────────────

function uptime(startedAt: number): string {
  const s = Math.floor(Date.now() / 1000 - startedAt);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

const RUNTIME_SERVICES: { key: string; label: string; urlKey: keyof AgentRuntime }[] = [
  { key: "chat", label: "Chat API", urlKey: "chatApiUrl" },
  { key: "ingestion", label: "Ingestion", urlKey: "ingestionUrl" },
  { key: "sse_rest", label: "SSE", urlKey: "sseUrl" },
  { key: "chainlit", label: "Chat UI", urlKey: "chatUiUrl" },
];

function RuntimePanel({ agentId }: { agentId: string }) {
  const { data: rt, isLoading } = useQuery<AgentRuntime | null>({
    queryKey: ["agent-runtime-deploy", agentId],
    queryFn: () => getAgentRuntime(agentId),
    refetchInterval: 5000,
    enabled: !!agentId,
  });

  if (isLoading) return (
    <div className="flex items-center gap-2 text-xs text-zinc-500 py-2">
      <div className="w-3 h-3 rounded-full border-2 border-zinc-700 border-t-brand-500 animate-spin" />
      Connecting to runtime…
    </div>
  );

  if (!rt) return (
    <div className="text-xs text-amber-400 bg-amber-950 border border-amber-900 rounded-lg p-3">
      Runtime not found — agent may still be starting or may have stopped.
    </div>
  );

  const readinessColor =
    rt.readiness === "ready" ? "bg-green-950 text-green-400" :
      rt.readiness === "degraded" ? "bg-red-950 text-red-400" :
        "bg-yellow-950 text-yellow-400";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-zinc-500">
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full font-medium ${readinessColor}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${rt.readiness === "ready" ? "bg-green-500 animate-pulse" : rt.readiness === "degraded" ? "bg-red-500" : "bg-yellow-500 animate-pulse"}`} />
          {rt.readiness}
        </span>
        <span>slot {rt.slot} · up {uptime(rt.startedAt)}</span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {RUNTIME_SERVICES.filter(({ key }) => rt.ports?.[key]).map(({ key, label, urlKey }) => {
          const health = rt.health?.[key];
          const url = rt[urlKey] as string | undefined;
          const running = health === "running";
          return (
            <div
              key={key}
              className={`rounded-xl border p-3 transition-colors ${running ? "border-green-800 bg-green-950/30" : "border-red-900 bg-red-950/30"}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-semibold text-zinc-300">{label}</span>
                {running
                  ? <CheckCircle size={12} className="text-green-500" />
                  : <XCircle size={12} className="text-red-400" />}
              </div>
              <p className="text-xs text-zinc-500 font-mono">:{rt.ports[key]}</p>
              {url && running && (
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 flex items-center gap-0.5 text-[11px] text-brand-600 hover:underline truncate"
                >
                  <ExternalLink size={9} className="shrink-0" />
                  {url.replace(/^https?:\/\//, "")}
                </a>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function Editor() {
  const { id } = useParams<{ id?: string }>();
  const isNew = !id;
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [activeTab, setActiveTab] = useState<"config" | "kb" | "feedback" | "deploy">("config");

  // AI Generate state
  const [genPrompt, setGenPrompt] = useState("");
  const [genError, setGenError] = useState("");

  // Export modal state
  const [exportContent, setExportContent] = useState<{ title: string; content: string } | null>(null);

  // Deactivate confirmation
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);

  // Activation mode modal
  const [activateModeOpen, setActivateModeOpen] = useState(false);
  const [activatingMode, setActivatingMode] = useState<"local" | "docker" | null>(null);

  // Improve state (feedback loaded via query on tab activation)
  const [improveResult, setImproveResult] = useState<GeneratedConfig | null>(null);

  const { data: agent } = useQuery({
    queryKey: ["agent", id],
    queryFn: () => getAgent(id!),
    enabled: !isNew,
  });

  const { data: deployData, refetch: refetchDeploy } = useQuery({
    queryKey: ["deploy", id],
    queryFn: () => getDeployStatus(id!),
    enabled: !isNew,
  });

  // Live runtime state — used for ingestion routing so we always have the
  // correct per-agent service URL even when agent.config hasn't been patched yet.
  const { data: agentRuntime } = useQuery<AgentRuntime | null>({
    queryKey: ["agent-runtime", id],
    queryFn: () => getAgentRuntime(id!),
    enabled: !isNew,
    refetchInterval: 10_000,
  });

  // Auto-loads when user opens the Feedback tab; cached for 1 min
  const { data: feedbackData = [], isLoading: feedbackLoading, refetch: refetchFeedback } = useQuery({
    queryKey: ["feedback", id],
    queryFn: () => getAgentFeedback(id!),
    enabled: !isNew && activeTab === "feedback",
    staleTime: 60_000,
  });

  useEffect(() => {
    if (agent) setForm(toFormState(agent));
  }, [agent]);

  const set = (key: keyof FormState) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      setForm((f) => ({ ...f, [key]: e.target.value }));

  const saveMutation = useMutation({
    mutationFn: async (): Promise<Agent> => {
      const config = toConfig(form);
      const payload = {
        name: form.name.trim(),
        description: form.description.trim(),
        config,
      };
      if (isNew) {
        const created = await createAgent(payload);
        // Auto-assign a collection name derived from the new agent ID if the
        // user left it blank — do it as an immediate follow-up update so the
        // agent always has a stable, unique collection name from creation.
        if (!config.collectionName) {
          const derived = deriveCollectionName(created.id);
          return updateAgent(created.id, {
            config: { ...config, collectionName: derived },
          });
        }
        return created;
      }
      return updateAgent(id!, payload);
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["agent", saved.id] });
      navigate(isNew ? `/agents/${saved.id}` : "/", { replace: true });
    },
  });

  const deployMutation = useMutation({
    mutationFn: () => (currentStatus === "active" ? undeployAgent(id!) : deployAgent(id!)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      refetchDeploy();
      // Auto-close the activation modal when local-process activation succeeds
      setActivateModeOpen(false);
      setActivatingMode(null);
    },
  });

  const generateMutation = useMutation({
    mutationFn: () => generateAgentConfig(genPrompt),
    onSuccess: (gen) => {
      setForm((f) => applyGenerated(f, gen));
      setGenError("");
    },
    onError: (e) => setGenError(String(e)),
  });

  const improveMutation = useMutation({
    mutationFn: () => {
      const feedbackText = feedbackData
        .map((r) => `[Rating: ${r.rating}] ${r.comment}`)
        .join("\n");
      return improveAgentConfig(id!, feedbackText);
    },
    onSuccess: (gen) => setImproveResult(gen),
  });

  const exportMutation = useMutation({
    mutationFn: ({ format }: { format: "kubernetes" | "docker-compose" }) =>
      format === "kubernetes" ? exportKubernetes(id!) : exportDockerCompose(id!),
    onSuccess: (data, { format }) => {
      setExportContent({
        title: format === "kubernetes" ? "Kubernetes YAML" : "Docker Compose YAML",
        content: typeof data === "string" ? data : JSON.stringify(data, null, 2),
      });
    },
  });

  const dockerDeployMutation = useMutation<DockerDeployResult, Error>({
    mutationFn: () => dockerDeploy(id!),
    // 202 "deploying" means the job was accepted — start polling status
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docker-status", id] });
      // Auto-close the activation modal when docker deployment is accepted
      setActivateModeOpen(false);
      setActivatingMode(null);
    },
  });

  const dockerStopMutation = useMutation<DockerDeployResult, Error>({
    mutationFn: () => dockerDeployStop(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["docker-status", id] }),
  });

  const dockerStatusQuery = useQuery<DockerDeployStatus>({
    queryKey: ["docker-status", id],
    queryFn: () => dockerDeployStatus(id!),
    // refetchInterval as a function: poll frequently while deploying
    refetchInterval: (query) =>
      (dockerDeployMutation.isPending || query.state.data?.status === "deploying") ? 4000 : 15000,
    enabled: !isNew,
  });

  const isDockerDeploying =
    dockerDeployMutation.isPending ||
    dockerStatusQuery.data?.status === "deploying";

  // agent?.status is the canonical source of truth; deployRecord is stale historical data
  const currentStatus = agent?.status ?? "draft";

  // Save current form config to DB then restart the per-agent ingestion service.
  // Called by IngestPanel's "Apply & Restart" button so that the backend picks up
  // the latest (possibly unsaved) chunk-strategy / embedding settings.
  const applyAndRestartIngestion = async () => {
    if (!id) return;
    const config = toConfig(form);
    await updateAgent(id, { name: form.name.trim(), description: form.description.trim(), config });
    qc.invalidateQueries({ queryKey: ["agent", id] });
    qc.invalidateQueries({ queryKey: ["agents"] });
    await restartIngestion(id);
  };

  // Normalise mixed rating formats: numeric 1–5 or string "thumbsUp"/"thumbsDown"
  const normalizeRating = (raw: unknown): number | null => {
    if (raw === "thumbsUp") return 5;
    if (raw === "thumbsDown") return 0;
    const n = Number(raw);
    return isNaN(n) ? null : n;
  };
  const ratedData = feedbackData.map((r) => normalizeRating(r.rating)).filter((n): n is number => n !== null);

  const pctPositive = ratedData.length > 0
    ? Math.round(ratedData.filter((n) => n >= 4).length / ratedData.length * 100)
    : null;

  return (
    <div className="h-screen flex flex-col bg-zinc-950 overflow-hidden">
      {/* Header */}
      <header className="shrink-0 bg-zinc-900 border-b border-zinc-800 px-6 py-3">
        <div className="max-w-full flex items-center gap-4">
          <button
            onClick={() => navigate("/")}
            className="text-zinc-500 hover:text-zinc-200 transition-colors"
          >
            <ArrowLeft size={20} />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-lg font-bold text-zinc-100 truncate">
              {isNew ? "New Agent" : (agent?.name ?? "Edit Agent")}
            </h1>
            {!isNew && agent && (
              <p className="text-xs text-zinc-500">
                ID: {agent.id} · v{agent.version} ·{" "}
                <span className={currentStatus === "active" ? "text-green-500 font-medium" : "text-zinc-500"}>
                  {currentStatus}
                </span>
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <ThemeToggle />
            {/* Status + deploy + chat live in the header so they're always visible */}
            {!isNew && (
              <>
                <span
                  className={`text-xs font-medium px-2.5 py-1 rounded-full ${currentStatus === "active"
                    ? "bg-green-950 text-green-400"
                    : currentStatus === "archived"
                      ? "bg-red-950 text-red-400"
                      : "bg-zinc-800 text-zinc-400"
                    }`}
                >
                  {currentStatus}
                </span>

                {currentStatus === "active" ? (
                  <button
                    onClick={() => setConfirmDeactivate(true)}
                    disabled={deployMutation.isPending}
                    className="flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 bg-amber-950 hover:bg-amber-900 text-amber-400"
                  >
                    <Square size={13} /> {deployMutation.isPending ? "Deactivating…" : "Deactivate"}
                  </button>
                ) : (
                  <button
                    onClick={() => setActivateModeOpen(true)}
                    disabled={deployMutation.isPending || dockerDeployMutation.isPending}
                    className="flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 bg-green-500 hover:bg-green-600 text-white"
                  >
                    <Monitor size={13} /> {deployMutation.isPending || dockerDeployMutation.isPending ? "Activating…" : "Activate"}
                  </button>
                )}

                {currentStatus === "active" && (
                  <a
                    href={`${agent?.config?.chatUiUrl || import.meta.env.VITE_CHAINLIT_URL || "http://localhost:7080"}?agent_id=${agent?.id ?? ""}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 text-sm font-medium text-brand-600 hover:text-brand-700 border border-brand-200 hover:border-brand-400 px-3 py-1.5 rounded-lg transition-colors"
                    title="Open Chainlit to chat with this agent"
                  >
                    <MessageSquare size={13} /> Open Chat
                  </a>
                )}

                <div className="w-px h-5 bg-zinc-700" />
              </>
            )}

            <button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || !form.name.trim()}
              className="flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
            >
              <Save size={14} />
              {saveMutation.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </header>

      {/* ── Tab bar ─────────────────────────────────────────────────────── */}
      <div className="shrink-0 bg-zinc-900 border-b border-zinc-800 px-6 flex items-end">
        {(["config", ...(!isNew ? ["kb", "feedback", "deploy"] : [])] as const).map((tab) => {
          const labels: Record<string, string> = {
            config: "Config",
            kb: "Knowledge Base",
            feedback: "Feedback",
            deploy: "Deploy",
          };
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${activeTab === tab
                ? "border-brand-500 text-brand-600"
                : "border-transparent text-zinc-500 hover:text-zinc-200 hover:border-zinc-600"
                }`}
            >
              {labels[tab]}
              {tab === "feedback" && feedbackData.length > 0 && (
                <span className="ml-1.5 text-xs bg-zinc-700 text-zinc-400 px-1.5 py-0.5 rounded-full">
                  {feedbackData.length}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* ── Tab content ─────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-hidden flex flex-col min-h-0">

        {/* ════ CONFIG ════ */}
        {activeTab === "config" && (
          <div className="flex-1 overflow-hidden flex min-h-0">

            {/* Left: AI Generate + Name + Description + System Prompt */}
            <div className="flex-1 min-w-0 flex flex-col gap-4 p-6 overflow-y-auto">

              {isNew && (
                <section className="shrink-0 bg-gradient-to-br from-purple-950/40 to-indigo-950/40 rounded-xl border border-purple-800 p-4 space-y-3">
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-purple-600 flex items-center gap-1.5">
                    <Sparkles size={13} /> AI Generate
                  </h2>
                  <textarea
                    value={genPrompt}
                    onChange={(e) => setGenPrompt(e.target.value)}
                    rows={2}
                    placeholder="Describe what this agent should do — we'll generate a starter config."
                    className="input resize-none text-sm w-full"
                  />
                  {genError && <p className="text-xs text-red-500">{genError}</p>}
                  <button
                    onClick={() => generateMutation.mutate()}
                    disabled={generateMutation.isPending || !genPrompt.trim()}
                    className="flex items-center gap-1.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
                  >
                    <Sparkles size={13} />
                    {generateMutation.isPending ? "Generating…" : "Generate Config"}
                  </button>
                </section>
              )}

              <div className="shrink-0 grid grid-cols-2 gap-3">
                <Field label="Name *">
                  <input value={form.name} onChange={set("name")} placeholder="e.g. Customer Support Bot" className="input" />
                </Field>
                <Field label="Description">
                  <input value={form.description} onChange={set("description")} placeholder="One-liner shown on the gallery card" className="input" />
                </Field>
              </div>

              <div className="flex flex-col flex-1 min-h-0 space-y-1">
                <label className="block text-xs font-medium text-zinc-400">System Prompt</label>
                <textarea
                  value={form.systemPrompt}
                  onChange={set("systemPrompt")}
                  placeholder={"You are a helpful assistant.\n\nAnswer questions accurately using context from the knowledge base."}
                  className="flex-1 input resize-none font-mono text-xs leading-relaxed min-h-[160px]"
                />
              </div>

              {saveMutation.isError && (
                <p className="text-sm text-red-500 shrink-0">{String(saveMutation.error)}</p>
              )}
            </div>

            {/* Right: LLM settings */}
            <div className="w-72 shrink-0 border-l border-zinc-800 bg-zinc-900 overflow-y-auto p-5 space-y-4">
              <section className="bg-zinc-800 rounded-xl border border-zinc-700 p-4 space-y-3">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">LLM</h2>
                <Field label="Provider">
                  <select value={form.llmProvider} onChange={set("llmProvider")} className="input">
                    {PROVIDER_OPTIONS.map((p) => (<option key={p} value={p}>{p}</option>))}
                  </select>
                </Field>
                <Field label="Model">
                  <input
                    value={form.llmModel}
                    onChange={set("llmModel")}
                    placeholder={PROVIDER_MODEL_HINTS[form.llmProvider] ?? "model name"}
                    className="input"
                  />
                </Field>
                <Field label="Base URL">
                  <input value={form.llmBaseUrl} onChange={set("llmBaseUrl")} placeholder="http://localhost:11434" className="input" />
                </Field>
                <Field label="Temperature">
                  <input type="number" min={0} max={2} step={0.1} value={form.temperature} onChange={set("temperature")} className="input" />
                </Field>
              </section>

              {!isNew && agent && (
                <section className="bg-zinc-800 rounded-xl border border-zinc-700 p-4 space-y-2">
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Info</h2>
                  <dl className="text-xs text-zinc-500 space-y-1">
                    <Row label="ID"><span className="font-mono text-[10px] break-all">{agent.id}</span></Row>
                    <Row label="Version">v{agent.version}</Row>
                    <Row label="Updated">{new Date(agent.updated_at).toLocaleDateString()}</Row>
                  </dl>
                </section>
              )}
            </div>
          </div>
        )}

        {/* ════ KNOWLEDGE BASE ════ */}
        {activeTab === "kb" && (
          <div className="flex-1 overflow-y-auto p-8">
            <div className="max-w-2xl mx-auto space-y-5">
              <div>
                <h2 className="text-base font-semibold text-zinc-100">Knowledge Base</h2>
                <p className="text-sm text-zinc-400 mt-1">
                  Configure retrieval settings and ingest documents into the vector store.
                </p>
              </div>

              <section className="bg-zinc-900 rounded-xl border border-zinc-700 p-6 space-y-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Retrieval</h3>
                <Field label="Weaviate Collection">
                  <div className="relative">
                    <input
                      value={form.collectionName}
                      onChange={set("collectionName")}
                      placeholder="e.g. AgentAbc12345"
                      className="input pr-8"
                    />
                    {id && form.collectionName !== deriveCollectionName(id) && (
                      <button
                        type="button"
                        title="Reset to auto-generated name"
                        onClick={() => setForm((f) => ({ ...f, collectionName: deriveCollectionName(id) }))}
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-zinc-500 hover:text-brand-400 transition-colors"
                      >
                        ↺
                      </button>
                    )}
                  </div>
                  <p className="text-xs text-zinc-500 mt-1">
                    Auto-generated from the agent ID. You can override this, but it must match the collection used during ingestion.
                  </p>
                </Field>

                <Field label="Top K Results">
                  <input
                    type="number" min={1} max={50}
                    value={form.topK} onChange={set("topK")}
                    className="input w-28"
                  />
                  <p className="text-xs text-zinc-500 mt-1">
                    Number of nearest-neighbour chunks to retrieve per query (1–50).
                  </p>
                </Field>

                <Field label="Chunk Strategy">
                  <select value={form.chunkStrategy} onChange={set("chunkStrategy")} className="input">
                    {CHUNK_STRATEGY_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                  <p className="text-xs text-zinc-500 mt-1">
                    Applied at ingest time — changing this only affects newly ingested documents.
                  </p>
                </Field>

                {/* ── Embedding ── */}
                <div className="border-t border-zinc-700 pt-5 space-y-4">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Embedding</h4>

                  <Field label="Embedding Provider">
                    <select value={form.embeddingProvider} onChange={set("embeddingProvider")} className="input">
                      <option value="Ollama">Ollama</option>
                      <option value="OpenAI">OpenAI</option>
                      <option value="Custom">Custom</option>
                    </select>
                  </Field>

                  <Field label="Embedding Model">
                    <input
                      value={form.embeddingModel}
                      onChange={set("embeddingModel")}
                      placeholder="e.g. nomic-embed-text"
                      className="input"
                    />
                    <p className="text-xs text-zinc-500 mt-1">
                      Must match the model used when documents were ingested. Default: <code>nomic-embed-text</code>.
                    </p>
                  </Field>

                  <Field label="Embedding Base URL">
                    <input
                      value={form.embeddingBaseUrl}
                      onChange={set("embeddingBaseUrl")}
                      placeholder="e.g. http://localhost:11434/v1"
                      className="input"
                    />
                    <p className="text-xs text-zinc-500 mt-1">
                      Leave blank to use the default Ollama endpoint (<code>http://ollama:11434/v1</code>).
                    </p>
                  </Field>
                </div>
              </section>

              <IngestPanel
                collection={form.collectionName}
                chunkStrategy={form.chunkStrategy}
                embeddingModel={form.embeddingModel}
                embeddingProvider={form.embeddingProvider}
                embeddingBaseUrl={form.embeddingBaseUrl}
                ingestionUrl={agentRuntime?.ingestionUrl ?? agent?.config?.ingestionUrl}
                agentId={id}
                onApplyRestart={!isNew ? applyAndRestartIngestion : undefined}
              />
            </div>
          </div>
        )}

        {/* ════ FEEDBACK ════ */}
        {activeTab === "feedback" && (
          <div className="flex-1 overflow-y-auto p-6">
            <div className="max-w-2xl mx-auto space-y-5">
              <div>
                <h2 className="text-base font-semibold text-zinc-100">Feedback & Improve</h2>
                <p className="text-sm text-zinc-400 mt-1">
                  Users rate agent responses in the Chainlit chat. Use those ratings to generate an improved system prompt.
                </p>
              </div>

              {/* How to collect feedback */}
              <div className="bg-blue-950 border border-blue-800 rounded-xl p-4 flex items-start gap-3">
                <MessageSquare size={16} className="text-blue-400 shrink-0 mt-0.5" />
                <div className="text-sm text-blue-300">
                  <span className="font-medium">How feedback is collected:</span> After each chat response in Chainlit, users can give a thumbs up/down or a star rating. Those ratings are stored automatically and appear here.
                  {currentStatus === "active" && (
                    <a
                      href={`${agent?.config?.chatUiUrl || import.meta.env.VITE_CHAINLIT_URL || "http://localhost:7080"}?agent_id=${agent?.id ?? ""}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="ml-2 underline hover:text-blue-600"
                    >
                      Open chat →
                    </a>
                  )}
                </div>
              </div>

              {feedbackLoading && (
                <div className="text-center py-12 text-zinc-500 text-sm">Loading feedback…</div>
              )}

              {!feedbackLoading && feedbackData.length === 0 && (
                <div className="text-center py-16">
                  <MessageSquare size={36} className="mx-auto mb-3 text-zinc-700" />
                  <p className="text-zinc-400 font-medium">No feedback yet</p>
                  <p className="text-zinc-600 text-sm mt-1">Activate this agent and share it with users to collect ratings.</p>
                </div>
              )}

              {feedbackData.length > 0 && (
                <>
                  {/* Summary stats */}
                  <div className="grid grid-cols-3 gap-3">
                    {[
                      { label: "Total Responses", value: feedbackData.length },
                      { label: "% Positive", value: pctPositive !== null ? `${pctPositive}%` : "—" },
                      { label: "👍 Positive", value: ratedData.filter((n) => n >= 4).length + " / " + ratedData.length },
                    ].map(({ label, value }) => (
                      <div key={label} className="bg-zinc-900 rounded-xl border border-zinc-700 p-4 text-center">
                        <div className="text-xl font-bold text-zinc-100">{value}</div>
                        <div className="text-xs text-zinc-500 mt-0.5">{label}</div>
                      </div>
                    ))}
                  </div>

                  {/* Refresh */}
                  <div className="flex items-center justify-between">
                    <p className="text-xs text-zinc-500">{feedbackData.length} response{feedbackData.length !== 1 ? "s" : ""}</p>
                    <button
                      onClick={() => refetchFeedback()}
                      className="text-xs text-zinc-500 hover:text-zinc-200 flex items-center gap-1 border border-zinc-700 px-2 py-1 rounded-lg hover:border-zinc-500 transition-colors"
                    >
                      ↻ Refresh
                    </button>
                  </div>

                  {/* Records list */}
                  <section className="bg-zinc-900 rounded-xl border border-zinc-700 divide-y divide-zinc-800">
                    {feedbackData.map((r, i) => (
                      <div key={i} className="flex items-start gap-3 px-4 py-3">
                        <span className="shrink-0 text-base">
                          {r.rating === "thumbsUp" ? "👍" : r.rating === "thumbsDown" ? "👎" : `★${r.rating}`}
                        </span>
                        <p className="text-sm text-zinc-300 flex-1">{r.comment || <span className="text-zinc-600 italic">no comment</span>}</p>
                        <span className="text-xs text-zinc-600 shrink-0 font-mono">{r.sessionId.slice(0, 8)}</span>
                      </div>
                    ))}
                  </section>

                  {/* Improve with AI */}
                  <section className="bg-zinc-900 rounded-xl border border-zinc-700 p-5 space-y-3">
                    <h3 className="text-sm font-semibold text-zinc-100">Improve with AI</h3>
                    <p className="text-xs text-zinc-400">
                      Send all feedback to the agent-builder service to generate an improved system prompt and config.
                    </p>
                    <button
                      onClick={() => improveMutation.mutate()}
                      disabled={improveMutation.isPending}
                      className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
                    >
                      <Sparkles size={14} />
                      {improveMutation.isPending ? "Generating suggestions…" : "Generate Improvements"}
                    </button>
                    {improveMutation.isError && (
                      <p className="text-xs text-red-500">{String(improveMutation.error)}</p>
                    )}
                    {improveResult && (
                      <div className="border border-indigo-800 rounded-lg p-4 bg-indigo-950/50 space-y-3">
                        <p className="text-sm font-medium text-indigo-300">Suggested improvements ready</p>
                        <pre className="text-xs text-zinc-300 whitespace-pre-wrap max-h-40 overflow-y-auto font-mono bg-zinc-900 rounded-lg p-3 border border-indigo-900">
                          {JSON.stringify(improveResult.config, null, 2)}
                        </pre>
                        <div className="flex gap-2">
                          <button
                            onClick={() => { setForm((f) => applyGenerated(f, improveResult)); setImproveResult(null); setActiveTab("config"); }}
                            className="text-sm bg-indigo-600 hover:bg-indigo-700 text-white font-medium px-4 py-1.5 rounded-lg transition-colors"
                          >
                            Apply & go to Config
                          </button>
                          <button
                            onClick={() => setImproveResult(null)}
                            className="text-sm text-zinc-400 hover:text-zinc-200 px-3 py-1.5 rounded-lg border border-zinc-700 hover:border-zinc-500 transition-colors"
                          >
                            Dismiss
                          </button>
                        </div>
                      </div>
                    )}
                  </section>
                </>
              )}
            </div>
          </div>
        )}

        {/* ════ DEPLOY ════ */}
        {activeTab === "deploy" && (
          <div className="flex-1 overflow-y-auto p-6">
            <div className="max-w-lg mx-auto space-y-5">
              <div>
                <h2 className="text-base font-semibold text-zinc-100">Deploy</h2>
                <p className="text-sm text-zinc-400 mt-1">
                  Activate this agent to make it available in Chainlit, or export it to run anywhere.
                </p>
              </div>

              {/* ── Runtime health (shown when active) ── */}
              {currentStatus === "active" && !isNew && id && (
                <section className="bg-zinc-900 rounded-xl border border-green-800 p-5 space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                      Live Runtime
                    </h3>
                    <button
                      onClick={() => setConfirmDeactivate(true)}
                      disabled={deployMutation.isPending}
                      className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 bg-amber-950 hover:bg-amber-900 text-amber-400"
                    >
                      <Square size={11} /> {deployMutation.isPending ? "Deactivating…" : "Deactivate"}
                    </button>
                  </div>
                  <RuntimePanel agentId={id} />
                  {agent?.config?.chatUiUrl && (
                    <a
                      href={`${agent.config.chatUiUrl}?agent_id=${agent.id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center justify-center gap-2 w-full py-2 rounded-lg text-sm font-medium text-brand-600 hover:text-brand-700 border border-brand-200 hover:border-brand-400 transition-colors"
                    >
                      <MessageSquare size={14} /> Open Chainlit Chat
                    </a>
                  )}
                </section>
              )}

              {/* ── Activate (shown when NOT active) ── */}
              {currentStatus !== "active" && (
                <section className="bg-zinc-900 rounded-xl border border-zinc-700 p-6 space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-zinc-100">Agent Status</p>
                      <p className="text-xs text-zinc-500 mt-0.5">Inactive — activate to allow chat sessions.</p>
                    </div>
                    <span className={`text-sm font-semibold px-3 py-1 rounded-full ${currentStatus === "archived" ? "bg-red-950 text-red-400" : "bg-zinc-800 text-zinc-400"
                      }`}>
                      {currentStatus}
                    </span>
                  </div>
                  <button
                    onClick={() => setActivateModeOpen(true)}
                    disabled={deployMutation.isPending || dockerDeployMutation.isPending}
                    className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50 bg-green-500 hover:bg-green-600 text-white"
                  >
                    <Monitor size={15} /> {deployMutation.isPending || dockerDeployMutation.isPending ? "Activating…" : "Activate Agent"}
                  </button>
                </section>
              )}

              {/* Export */}
              <section className="bg-zinc-900 rounded-xl border border-zinc-700 p-6 space-y-4">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-100">Export</h3>
                  <p className="text-xs text-zinc-400 mt-1">Download deployment manifests to run this agent on your own infrastructure.</p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    onClick={() => exportMutation.mutate({ format: "kubernetes" })}
                    disabled={exportMutation.isPending}
                    className="flex items-center justify-center gap-1.5 text-sm text-zinc-300 hover:text-zinc-100 border border-zinc-700 hover:border-zinc-500 px-4 py-2.5 rounded-lg transition-colors disabled:opacity-50"
                  >
                    <Download size={14} /> Kubernetes YAML
                  </button>
                  <button
                    onClick={() => exportMutation.mutate({ format: "docker-compose" })}
                    disabled={exportMutation.isPending}
                    className="flex items-center justify-center gap-1.5 text-sm text-zinc-300 hover:text-zinc-100 border border-zinc-700 hover:border-zinc-500 px-4 py-2.5 rounded-lg transition-colors disabled:opacity-50"
                  >
                    <Download size={14} /> Docker Compose
                  </button>
                </div>
                {exportMutation.isError && (
                  <p className="text-xs text-red-500">{String(exportMutation.error)}</p>
                )}
              </section>

              {/* Docker Compose Management */}
              <section className="bg-zinc-900 rounded-xl border border-zinc-700 p-6 space-y-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="text-sm font-semibold text-zinc-100">Docker Containers</h3>
                    <p className="text-xs text-zinc-400 mt-1">
                      Manage a running Docker Compose deployment.
                      {currentStatus !== "active" && <span className="text-zinc-600"> Activate via header → choose Docker to start.</span>}
                    </p>
                  </div>
                  {dockerStatusQuery.data && (
                    <span className={[
                      "shrink-0 text-xs font-medium px-2 py-0.5 rounded-full",
                      dockerStatusQuery.data.status === "running"
                        ? "bg-green-950 text-green-400"
                        : dockerStatusQuery.data.status === "stopped"
                          ? "bg-yellow-950 text-yellow-400"
                          : "bg-zinc-800 text-zinc-500",
                    ].join(" ")}>
                      {dockerStatusQuery.data.status}
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  {dockerStatusQuery.data?.status === "running" && (
                    <button
                      onClick={() => dockerStopMutation.mutate()}
                      disabled={dockerStopMutation.isPending}
                      className="flex items-center gap-1.5 text-sm font-medium text-red-400 border border-red-900 hover:border-red-700 px-4 py-2 rounded-lg transition-colors disabled:opacity-50"
                    >
                      <CircleStop size={14} />
                      {dockerStopMutation.isPending ? "Stopping…" : "Stop Containers"}
                    </button>
                  )}
                  {isDockerDeploying && (
                    <span className="flex items-center gap-1.5 text-xs text-brand-600 font-medium">
                      <RefreshCw size={12} className="animate-spin" /> Deploying…
                    </span>
                  )}
                  <button
                    onClick={() => qc.invalidateQueries({ queryKey: ["docker-status", id] })}
                    className="ml-auto text-zinc-500 hover:text-zinc-300 transition-colors"
                    title="Refresh status"
                  >
                    <RefreshCw size={14} />
                  </button>
                </div>

                {/* containers table */}
                {dockerStatusQuery.data?.containers && dockerStatusQuery.data.containers.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-left text-zinc-500 border-b border-zinc-700">
                          <th className="pb-1 pr-4 font-medium">Container</th>
                          <th className="pb-1 pr-4 font-medium">State</th>
                          <th className="pb-1 font-medium">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dockerStatusQuery.data.containers.map((c, i) => (
                          <tr key={i} className="border-b border-zinc-800">
                            <td className="py-1 pr-4 font-mono text-zinc-300 truncate max-w-[140px]">{c.Name}</td>
                            <td className="py-1 pr-4">
                              <span className={c.State === "running" ? "text-green-400" : "text-yellow-400"}>{c.State}</span>
                            </td>
                            <td className="py-1 text-zinc-500">{c.Status}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* output log on deploy */}
                {(dockerDeployMutation.data || dockerDeployMutation.isError) && (
                  <div className="bg-gray-900 rounded-lg p-3 text-xs font-mono text-gray-100 max-h-40 overflow-y-auto whitespace-pre-wrap break-all">
                    {dockerDeployMutation.isError
                      ? String(dockerDeployMutation.error)
                      : [dockerDeployMutation.data?.stdout, dockerDeployMutation.data?.stderr]
                        .filter(Boolean).join("\n").trim() || (dockerDeployMutation.data?.success ? "✓ Started" : dockerDeployMutation.data?.error)}
                  </div>
                )}

                {dockerStopMutation.isError && (
                  <p className="text-xs text-red-500">{String(dockerStopMutation.error)}</p>
                )}
              </section>
            </div>
          </div>
        )}

      </div>

      {exportContent && (
        <ExportModal
          title={exportContent.title}
          content={exportContent.content}
          onClose={() => setExportContent(null)}
        />
      )}

      {activateModeOpen && (
        <ActivationModeModal
          onLocal={() => { setActivatingMode("local"); deployMutation.mutate(); }}
          onDocker={() => { setActivatingMode("docker"); dockerDeployMutation.mutate(); }}
          onClose={() => { setActivateModeOpen(false); setActivatingMode(null); }}
          activatingMode={activatingMode}
          error={
            activatingMode === "local" && deployMutation.isError ? String(deployMutation.error) :
              activatingMode === "docker" && dockerDeployMutation.isError ? String(dockerDeployMutation.error) :
                undefined
          }
        />
      )}

      {confirmDeactivate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-zinc-900 rounded-xl shadow-2xl w-full max-w-sm p-6 space-y-4">
            <div className="flex items-start gap-3">
              <div className="shrink-0 bg-amber-950 rounded-full p-2">
                <AlertTriangle size={20} className="text-amber-500" />
              </div>
              <div>
                <h3 className="font-semibold text-zinc-100">Deactivate agent?</h3>
                <p className="text-sm text-zinc-400 mt-1">
                  This will stop all running services for <span className="font-medium text-zinc-200">{agent?.name}</span>.
                  Active chat sessions will be disconnected.
                </p>
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setConfirmDeactivate(false)}
                className="px-4 py-2 text-sm font-medium text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => { setConfirmDeactivate(false); deployMutation.mutate(); }}
                className="px-4 py-2 text-sm font-medium text-white bg-amber-500 hover:bg-amber-600 rounded-lg transition-colors"
              >
                Deactivate
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="block text-xs font-medium text-zinc-400">{label}</label>
      {children}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 font-medium">{label}</dt>
      <dd className="truncate">{children}</dd>
    </div>
  );
}

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Save, Play, Square, Download, Sparkles, MessageSquare } from "lucide-react";
import {
  createAgent,
  deployAgent,
  exportDockerCompose,
  exportKubernetes,
  generateAgentConfig,
  getAgent,
  getAgentFeedback,
  getDeployStatus,
  improveAgentConfig,
  ingestDocuments,
  ingestGitHub,
  ingestUrl,
  undeployAgent,
  updateAgent,
} from "../api";
import type { Agent, AgentConfig, FeedbackRecord, GeneratedConfig } from "../types";

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
  llmProvider: "Ollama",
  llmModel: "",
  llmBaseUrl: "http://localhost:11434",
  temperature: "0.7",
};

function toFormState(agent: Agent): FormState {
  const c = agent.config ?? {};
  return {
    name: agent.name,
    description: agent.description,
    systemPrompt: c.systemPrompt ?? "",
    collectionName: c.collectionName ?? "",
    topK: String(c.topK ?? 5),
    chunkStrategy: c.chunkStrategy ?? "sentence",
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
    llmProvider: form.llmProvider || undefined,
    llmModel: form.llmModel || undefined,
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
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[80vh]">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="font-semibold text-gray-900">{title}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl leading-none">&times;</button>
        </div>
        <pre className="flex-1 overflow-auto p-5 text-xs font-mono bg-gray-50 rounded-b-xl whitespace-pre-wrap">{content}</pre>
      </div>
    </div>
  );
}

// ── Ingest Panel ─────────────────────────────────────────────────────────────

type IngestSubTab = "text" | "url" | "github" | "api";

function IngestPanel({ collection, chunkStrategy }: { collection: string; chunkStrategy: string }) {
  const [tab, setTab] = useState<IngestSubTab>("text");
  const [pasteText, setPasteText] = useState("");
  const [pasteSource, setPasteSource] = useState("");
  const [urlValue, setUrlValue] = useState("");
  const [ghOwner, setGhOwner] = useState("");
  const [ghRepo, setGhRepo] = useState("");
  const [ghPath, setGhPath] = useState("");
  const [ghBranch, setGhBranch] = useState("main");

  const textMut = useMutation({
    mutationFn: () =>
      ingestDocuments(
        collection,
        [{ text: pasteText.trim(), source: pasteSource.trim() || "pasted-text.txt" }],
        chunkStrategy || undefined,
      ),
  });

  const urlMut = useMutation({
    mutationFn: () => ingestUrl(collection, urlValue.trim(), chunkStrategy || undefined),
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
      ),
  });

  const isPending = textMut.isPending || urlMut.isPending || githubMut.isPending;
  const result = textMut.data ?? urlMut.data ?? githubMut.data ?? null;
  const mutError = textMut.error ?? urlMut.error ?? githubMut.error ?? null;

  const INGEST_HOST = "http://localhost:7002";
  const AUTH_HEADER = "Basic ZmxvZ286Y2hhbmdlbWU=";
  const coll = collection || "MyCollection";
  const strategy = chunkStrategy || "sentence";

  const TABS: { id: IngestSubTab; label: string }[] = [
    { id: "text", label: "Paste Text" },
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
    <section className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Ingest Documents</h3>
        <span
          className={`text-xs px-2 py-0.5 rounded font-mono ${collection
            ? "bg-green-50 text-green-700 border border-green-200"
            : "bg-amber-50 text-amber-700 border border-amber-200"
            }`}
        >
          {collection || "no collection set"}
        </span>
      </div>

      {!collection && (
        <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-3">
          Set a collection name in <strong>Retrieval</strong> above and save the agent before ingesting documents.
        </div>
      )}

      {/* Sub-tab bar */}
      <div className="flex gap-0 border-b border-gray-100">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors -mb-px ${tab === id
              ? "border-brand-500 text-brand-600"
              : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-200"
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
          <button
            onClick={() => textMut.mutate()}
            disabled={!pasteText.trim() || !collection || isPending}
            className="flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
          >
            {textMut.isPending ? "Ingesting…" : "Ingest Document"}
          </button>
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
            <p className="text-xs text-gray-400 mt-1">
              The page will be fetched via HTTP GET and its content split into chunks.
            </p>
          </Field>
          <button
            onClick={() => urlMut.mutate()}
            disabled={!urlValue.trim() || !collection || isPending}
            className="flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
          >
            {urlMut.isPending ? "Fetching & ingesting…" : "Fetch & Ingest"}
          </button>
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
          <p className="text-xs text-gray-400">Recursively ingests all Markdown and text files at the given path.</p>
          <button
            onClick={() => githubMut.mutate()}
            disabled={!ghOwner || !ghRepo || !ghPath || !collection || isPending}
            className="flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
          >
            {githubMut.isPending ? "Ingesting from GitHub…" : "Ingest from GitHub"}
          </button>
        </div>
      )}

      {/* API Reference */}
      {tab === "api" && (
        <div className="space-y-5">
          <p className="text-xs text-gray-500">
            Call these endpoints directly from scripts, CI/CD pipelines, or external tools.
            The ingestion service runs on{" "}
            <code className="font-mono bg-gray-100 px-1 rounded">http://localhost:7002</code>.
          </p>
          {API_EXAMPLES.map(({ label, endpoint, body }) => (
            <div key={endpoint}>
              <div className="flex items-baseline gap-2 mb-1.5">
                <code className="text-xs font-semibold text-gray-700">POST {endpoint}</code>
                <span className="text-xs text-gray-400">— {label}</span>
              </div>
              <pre className="bg-gray-900 text-green-300 rounded-lg p-3 overflow-x-auto font-mono text-[11px] leading-relaxed whitespace-pre">{`curl -X POST ${INGEST_HOST}${endpoint} \\\n  -H "Authorization: ${AUTH_HEADER}" \\\n  -H "Content-Type: application/json" \\\n  -d '${body}'`}</pre>
            </div>
          ))}
        </div>
      )}

      {/* Result / error feedback */}
      {result && (
        <div className="text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg p-3 font-mono whitespace-pre-wrap">
          {result}
        </div>
      )}
      {mutError && (
        <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">
          {String(mutError)}
        </div>
      )}
    </section>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function Editor() {
  const { id } = useParams<{ id?: string }>();
  const isNew = !id;
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [savedFeedback, setSavedFeedback] = useState(false);
  const [activeTab, setActiveTab] = useState<"config" | "kb" | "feedback" | "deploy">("config");

  // AI Generate state
  const [genPrompt, setGenPrompt] = useState("");
  const [genError, setGenError] = useState("");

  // Export modal state
  const [exportContent, setExportContent] = useState<{ title: string; content: string } | null>(null);

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
      const payload = {
        name: form.name.trim(),
        description: form.description.trim(),
        config: toConfig(form),
      };
      return isNew ? createAgent(payload) : updateAgent(id!, payload);
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["agent", saved.id] });
      setSavedFeedback(true);
      setTimeout(() => setSavedFeedback(false), 2500);
      if (isNew) navigate(`/agents/${saved.id}`, { replace: true });
    },
  });

  const deployMutation = useMutation({
    mutationFn: () => (currentStatus === "active" ? undeployAgent(id!) : deployAgent(id!)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      refetchDeploy();
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

  const deployRecord = deployData?.records?.[0];
  const currentStatus = deployRecord?.status ?? agent?.status ?? "draft";

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
    <div className="h-screen flex flex-col bg-gray-50 overflow-hidden">
      {/* Header */}
      <header className="shrink-0 bg-white border-b border-gray-200 px-6 py-3">
        <div className="max-w-full flex items-center gap-4">
          <button
            onClick={() => navigate("/")}
            className="text-gray-400 hover:text-gray-700 transition-colors"
          >
            <ArrowLeft size={20} />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-lg font-bold text-gray-900 truncate">
              {isNew ? "New Agent" : (agent?.name ?? "Edit Agent")}
            </h1>
            {!isNew && agent && (
              <p className="text-xs text-gray-400">
                ID: {agent.id} · v{agent.version} ·{" "}
                <span className={currentStatus === "active" ? "text-green-600 font-medium" : "text-gray-400"}>
                  {currentStatus}
                </span>
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {/* Status + deploy + chat live in the header so they're always visible */}
            {!isNew && (
              <>
                <span
                  className={`text-xs font-medium px-2.5 py-1 rounded-full ${currentStatus === "active"
                    ? "bg-green-100 text-green-800"
                    : currentStatus === "archived"
                      ? "bg-red-50 text-red-600"
                      : "bg-gray-100 text-gray-600"
                    }`}
                >
                  {currentStatus}
                </span>

                <button
                  onClick={() => deployMutation.mutate()}
                  disabled={deployMutation.isPending}
                  className={`flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 ${currentStatus === "active"
                    ? "bg-amber-100 hover:bg-amber-200 text-amber-800"
                    : "bg-green-500 hover:bg-green-600 text-white"
                    }`}
                >
                  {currentStatus === "active"
                    ? <><Square size={13} /> {deployMutation.isPending ? "Deactivating…" : "Deactivate"}</>
                    : <><Play size={13} /> {deployMutation.isPending ? "Activating…" : "Activate"}</>
                  }
                </button>

                {currentStatus === "active" && (
                  <a
                    href={`${import.meta.env.VITE_CHAINLIT_URL ?? "http://localhost:7080"}?agent_id=${agent?.id ?? ""}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 text-sm font-medium text-brand-600 hover:text-brand-700 border border-brand-200 hover:border-brand-400 px-3 py-1.5 rounded-lg transition-colors"
                    title="Open Chainlit to chat with this agent"
                  >
                    <MessageSquare size={13} /> Open Chat
                  </a>
                )}

                <div className="w-px h-5 bg-gray-200" />
              </>
            )}

            <button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || !form.name.trim()}
              className="flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
            >
              <Save size={14} />
              {saveMutation.isPending ? "Saving…" : savedFeedback ? "Saved ✓" : "Save"}
            </button>
          </div>
        </div>
      </header>

      {/* ── Tab bar ─────────────────────────────────────────────────────── */}
      <div className="shrink-0 bg-white border-b border-gray-200 px-6 flex items-end">
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
                : "border-transparent text-gray-500 hover:text-gray-800 hover:border-gray-300"
                }`}
            >
              {labels[tab]}
              {tab === "feedback" && feedbackData.length > 0 && (
                <span className="ml-1.5 text-xs bg-gray-200 text-gray-600 px-1.5 py-0.5 rounded-full">
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
                <section className="shrink-0 bg-gradient-to-br from-purple-50 to-indigo-50 rounded-xl border border-purple-200 p-4 space-y-3">
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
                <label className="block text-xs font-medium text-gray-600">System Prompt</label>
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
            <div className="w-72 shrink-0 border-l border-gray-200 bg-gray-50 overflow-y-auto p-5 space-y-4">
              <section className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">LLM</h2>
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
                <section className="bg-white rounded-xl border border-gray-200 p-4 space-y-2">
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">Info</h2>
                  <dl className="text-xs text-gray-400 space-y-1">
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
                <h2 className="text-base font-semibold text-gray-900">Knowledge Base</h2>
                <p className="text-sm text-gray-500 mt-1">
                  Configure retrieval settings and ingest documents into the vector store.
                </p>
              </div>

              <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400">Retrieval</h3>
                <Field label="Weaviate Collection">
                  <input
                    value={form.collectionName}
                    onChange={set("collectionName")}
                    placeholder="e.g. KnowledgeBase"
                    className="input"
                  />
                  <p className="text-xs text-gray-400 mt-1">
                    Must match the collection name used during document ingestion.
                  </p>
                </Field>

                <Field label="Top K Results">
                  <input
                    type="number" min={1} max={50}
                    value={form.topK} onChange={set("topK")}
                    className="input w-28"
                  />
                  <p className="text-xs text-gray-400 mt-1">
                    Number of nearest-neighbour chunks to retrieve per query (1–50).
                  </p>
                </Field>

                <Field label="Chunk Strategy">
                  <select value={form.chunkStrategy} onChange={set("chunkStrategy")} className="input">
                    {CHUNK_STRATEGY_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                  <p className="text-xs text-gray-400 mt-1">
                    Applied at ingest time — changing this only affects newly ingested documents.
                  </p>
                </Field>
              </section>

              <IngestPanel collection={form.collectionName} chunkStrategy={form.chunkStrategy} />
            </div>
          </div>
        )}

        {/* ════ FEEDBACK ════ */}
        {activeTab === "feedback" && (
          <div className="flex-1 overflow-y-auto p-6">
            <div className="max-w-2xl mx-auto space-y-5">
              <div>
                <h2 className="text-base font-semibold text-gray-900">Feedback & Improve</h2>
                <p className="text-sm text-gray-500 mt-1">
                  Users rate agent responses in the Chainlit chat. Use those ratings to generate an improved system prompt.
                </p>
              </div>

              {/* How to collect feedback */}
              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 flex items-start gap-3">
                <MessageSquare size={16} className="text-blue-500 shrink-0 mt-0.5" />
                <div className="text-sm text-blue-800">
                  <span className="font-medium">How feedback is collected:</span> After each chat response in Chainlit, users can give a thumbs up/down or a star rating. Those ratings are stored automatically and appear here.
                  {currentStatus === "active" && (
                    <a
                      href={`${import.meta.env.VITE_CHAINLIT_URL ?? "http://localhost:7080"}?agent_id=${agent?.id ?? ""}`}
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
                <div className="text-center py-12 text-gray-400 text-sm">Loading feedback…</div>
              )}

              {!feedbackLoading && feedbackData.length === 0 && (
                <div className="text-center py-16">
                  <MessageSquare size={36} className="mx-auto mb-3 text-gray-300" />
                  <p className="text-gray-500 font-medium">No feedback yet</p>
                  <p className="text-gray-400 text-sm mt-1">Activate this agent and share it with users to collect ratings.</p>
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
                      <div key={label} className="bg-white rounded-xl border border-gray-200 p-4 text-center">
                        <div className="text-xl font-bold text-gray-900">{value}</div>
                        <div className="text-xs text-gray-500 mt-0.5">{label}</div>
                      </div>
                    ))}
                  </div>

                  {/* Refresh */}
                  <div className="flex items-center justify-between">
                    <p className="text-xs text-gray-400">{feedbackData.length} response{feedbackData.length !== 1 ? "s" : ""}</p>
                    <button
                      onClick={() => refetchFeedback()}
                      className="text-xs text-gray-500 hover:text-gray-800 flex items-center gap-1 border border-gray-200 px-2 py-1 rounded-lg hover:border-gray-400 transition-colors"
                    >
                      ↻ Refresh
                    </button>
                  </div>

                  {/* Records list */}
                  <section className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100">
                    {feedbackData.map((r, i) => (
                      <div key={i} className="flex items-start gap-3 px-4 py-3">
                        <span className="shrink-0 text-base">
                          {r.rating === "thumbsUp" ? "👍" : r.rating === "thumbsDown" ? "👎" : `★${r.rating}`}
                        </span>
                        <p className="text-sm text-gray-700 flex-1">{r.comment || <span className="text-gray-400 italic">no comment</span>}</p>
                        <span className="text-xs text-gray-400 shrink-0 font-mono">{r.sessionId.slice(0, 8)}</span>
                      </div>
                    ))}
                  </section>

                  {/* Improve with AI */}
                  <section className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
                    <h3 className="text-sm font-semibold text-gray-900">Improve with AI</h3>
                    <p className="text-xs text-gray-500">
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
                      <div className="border border-indigo-200 rounded-lg p-4 bg-indigo-50 space-y-3">
                        <p className="text-sm font-medium text-indigo-800">Suggested improvements ready</p>
                        <pre className="text-xs text-gray-700 whitespace-pre-wrap max-h-40 overflow-y-auto font-mono bg-white rounded-lg p-3 border border-indigo-100">
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
                            className="text-sm text-gray-500 hover:text-gray-800 px-3 py-1.5 rounded-lg border border-gray-200 hover:border-gray-400 transition-colors"
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
                <h2 className="text-base font-semibold text-gray-900">Deploy</h2>
                <p className="text-sm text-gray-500 mt-1">
                  Activate this agent to make it available in Chainlit, or export it to run anywhere.
                </p>
              </div>

              {/* Status card */}
              <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-5">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-gray-900">Agent Status</p>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {currentStatus === "active"
                        ? "Running — users can chat with this agent via Chainlit."
                        : "Inactive — activate to allow chat sessions."}
                    </p>
                  </div>
                  <span className={`text-sm font-semibold px-3 py-1 rounded-full ${currentStatus === "active" ? "bg-green-100 text-green-800"
                    : currentStatus === "archived" ? "bg-red-50 text-red-600"
                      : "bg-gray-100 text-gray-600"
                    }`}>
                    {currentStatus}
                  </span>
                </div>

                <button
                  onClick={() => deployMutation.mutate()}
                  disabled={deployMutation.isPending}
                  className={`w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50 ${currentStatus === "active"
                    ? "bg-amber-100 hover:bg-amber-200 text-amber-800"
                    : "bg-green-500 hover:bg-green-600 text-white"
                    }`}
                >
                  {currentStatus === "active"
                    ? <><Square size={15} /> {deployMutation.isPending ? "Deactivating…" : "Deactivate Agent"}</>
                    : <><Play size={15} /> {deployMutation.isPending ? "Activating…" : "Activate Agent"}</>
                  }
                </button>

                {currentStatus === "active" && (
                  <a
                    href={import.meta.env.VITE_CHAINLIT_URL ?? "http://localhost:7080"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-center gap-2 w-full py-2.5 rounded-lg text-sm font-medium text-brand-600 hover:text-brand-700 border border-brand-200 hover:border-brand-400 transition-colors"
                  >
                    <MessageSquare size={15} /> Open Chainlit Chat
                  </a>
                )}
              </section>

              {/* Export */}
              <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Export</h3>
                  <p className="text-xs text-gray-500 mt-1">Download deployment manifests to run this agent on your own infrastructure.</p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    onClick={() => exportMutation.mutate({ format: "kubernetes" })}
                    disabled={exportMutation.isPending}
                    className="flex items-center justify-center gap-1.5 text-sm text-gray-700 hover:text-gray-900 border border-gray-200 hover:border-gray-400 px-4 py-2.5 rounded-lg transition-colors disabled:opacity-50"
                  >
                    <Download size={14} /> Kubernetes YAML
                  </button>
                  <button
                    onClick={() => exportMutation.mutate({ format: "docker-compose" })}
                    disabled={exportMutation.isPending}
                    className="flex items-center justify-center gap-1.5 text-sm text-gray-700 hover:text-gray-900 border border-gray-200 hover:border-gray-400 px-4 py-2.5 rounded-lg transition-colors disabled:opacity-50"
                  >
                    <Download size={14} /> Docker Compose
                  </button>
                </div>
                {exportMutation.isError && (
                  <p className="text-xs text-red-500">{String(exportMutation.error)}</p>
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
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="block text-xs font-medium text-gray-600">{label}</label>
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

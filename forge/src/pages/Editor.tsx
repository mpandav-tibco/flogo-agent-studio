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
  undeployAgent,
  updateAgent,
} from "../api";
import type { Agent, AgentConfig, FeedbackRecord, GeneratedConfig } from "../types";

const PROVIDER_OPTIONS = ["Ollama", "OpenAI", "Anthropic", "Groq", "Custom"];

const PROVIDER_MODEL_HINTS: Record<string, string> = {
  Ollama:    "llama3.2:3b · llama3.1:8b · deepseek-r1:latest",
  OpenAI:    "gpt-4o · gpt-4o-mini · gpt-3.5-turbo",
  Anthropic: "claude-opus-4-7 · claude-sonnet-4-6 · claude-haiku-4-5-20251001",
  Groq:      "llama-3.3-70b-versatile",
  Custom:    "depends on your provider",
};

interface FormState {
  name: string;
  description: string;
  systemPrompt: string;
  collectionName: string;
  topK: string;
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

// ── Main ──────────────────────────────────────────────────────────────────────

export default function Editor() {
  const { id } = useParams<{ id?: string }>();
  const isNew = !id;
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [savedFeedback, setSavedFeedback] = useState(false);

  // AI Generate state
  const [genPrompt, setGenPrompt] = useState("");
  const [genError, setGenError] = useState("");

  // Export modal state
  const [exportContent, setExportContent] = useState<{ title: string; content: string } | null>(null);

  // Feedback/improve state
  const [feedbackData, setFeedbackData] = useState<FeedbackRecord[] | null>(null);
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

  const loadFeedbackMutation = useMutation({
    mutationFn: () => getAgentFeedback(id!),
    onSuccess: (records) => setFeedbackData(records),
  });

  const improveMutation = useMutation({
    mutationFn: () => {
      const feedbackText = (feedbackData ?? [])
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
      const records = data.records ?? [];
      const content = records.length > 0
        ? (typeof records[0] === "string" ? records[0] : JSON.stringify(records[0], null, 2))
        : "(no content returned)";
      setExportContent({
        title: format === "kubernetes" ? "Kubernetes YAML" : "Docker Compose YAML",
        content,
      });
    },
  });

  const deployRecord = deployData?.records?.[0];
  const currentStatus = deployRecord?.status ?? agent?.status ?? "draft";

  const avgRating = feedbackData && feedbackData.length > 0
    ? (feedbackData.reduce((sum, r) => sum + Number(r.rating || 0), 0) / feedbackData.length).toFixed(1)
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

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto p-6 space-y-6">

          {/* ── AI Generate (new agents only) ─────────────────────────────── */}
          {isNew && (
            <section className="bg-gradient-to-br from-purple-50 to-indigo-50 rounded-xl border border-purple-200 p-5 space-y-3">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-purple-600 flex items-center gap-1.5">
                <Sparkles size={13} /> AI Generate
              </h2>
              <p className="text-xs text-gray-500">Describe what this agent should do and we'll generate a starter config.</p>
              <textarea
                value={genPrompt}
                onChange={(e) => setGenPrompt(e.target.value)}
                rows={3}
                placeholder="e.g. A customer support agent for a SaaS product that answers questions from the knowledge base and escalates unresolved issues."
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

          {/* ── Identity ─────────────────────────────────────────────────── */}
          <section className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">Identity</h2>
            <Field label="Name *">
              <input
                value={form.name}
                onChange={set("name")}
                placeholder="e.g. Customer Support Bot"
                className="input"
              />
            </Field>
            <Field label="Description">
              <input
                value={form.description}
                onChange={set("description")}
                placeholder="One-liner shown on the gallery card"
                className="input"
              />
            </Field>
            <Field label="System Prompt">
              <textarea
                value={form.systemPrompt}
                onChange={set("systemPrompt")}
                rows={8}
                placeholder={
                  "You are a helpful assistant.\n\n" +
                  "Answer questions accurately using context from the knowledge base."
                }
                className="input resize-none font-mono text-xs leading-relaxed"
              />
            </Field>
          </section>

          {/* ── Knowledge Base ───────────────────────────────────────────── */}
          <section className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">Knowledge Base</h2>
            <Field label="Collection Name">
              <input
                value={form.collectionName}
                onChange={set("collectionName")}
                placeholder="e.g. KnowledgeBase"
                className="input"
              />
              <p className="text-xs text-gray-400 mt-0.5">
                Weaviate collection to query. Must match the ingestion collection name.
              </p>
            </Field>
            <Field label="Top K Results">
              <input
                type="number"
                min={1}
                max={50}
                value={form.topK}
                onChange={set("topK")}
                className="input"
              />
            </Field>
          </section>

          {/* ── LLM ─────────────────────────────────────────────────────── */}
          <section className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">LLM</h2>
            <Field label="Provider">
              <select value={form.llmProvider} onChange={set("llmProvider")} className="input">
                {PROVIDER_OPTIONS.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
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
              <input
                value={form.llmBaseUrl}
                onChange={set("llmBaseUrl")}
                placeholder="http://localhost:11434"
                className="input"
              />
            </Field>
            <Field label="Temperature">
              <input
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={form.temperature}
                onChange={set("temperature")}
                className="input"
              />
            </Field>
          </section>

          {/* ── Deploy (existing agents only) ────────────────────────────── */}
          {!isNew && (
            <section className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">Deploy</h2>

              <div className="flex items-center gap-3">
                <span
                  className={`text-xs font-medium px-2.5 py-1 rounded-full ${
                    currentStatus === "active"
                      ? "bg-green-100 text-green-800"
                      : "bg-gray-100 text-gray-600"
                  }`}
                >
                  {currentStatus}
                </span>

                <button
                  onClick={() => deployMutation.mutate()}
                  disabled={deployMutation.isPending}
                  className={`flex items-center gap-1.5 text-sm font-medium px-4 py-1.5 rounded-lg transition-colors disabled:opacity-50 ${
                    currentStatus === "active"
                      ? "bg-amber-100 hover:bg-amber-200 text-amber-800"
                      : "bg-green-500 hover:bg-green-600 text-white"
                  }`}
                >
                  {currentStatus === "active"
                    ? <><Square size={13} /> {deployMutation.isPending ? "Deactivating…" : "Deactivate"}</>
                    : <><Play size={13} /> {deployMutation.isPending ? "Activating…" : "Activate"}</>
                  }
                </button>
              </div>

              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => exportMutation.mutate({ format: "kubernetes" })}
                  disabled={exportMutation.isPending}
                  className="flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900 border border-gray-200 hover:border-gray-400 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                >
                  <Download size={13} /> Kubernetes YAML
                </button>
                <button
                  onClick={() => exportMutation.mutate({ format: "docker-compose" })}
                  disabled={exportMutation.isPending}
                  className="flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900 border border-gray-200 hover:border-gray-400 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                >
                  <Download size={13} /> Docker Compose
                </button>
              </div>

              {exportMutation.isError && (
                <p className="text-xs text-red-500">{String(exportMutation.error)}</p>
              )}
            </section>
          )}

          {/* ── Feedback & Improve (existing agents only) ─────────────────── */}
          {!isNew && (
            <section className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400 flex items-center gap-1.5">
                <MessageSquare size={13} /> Feedback & Improve
              </h2>

              <div className="flex items-center gap-3">
                <button
                  onClick={() => loadFeedbackMutation.mutate()}
                  disabled={loadFeedbackMutation.isPending}
                  className="text-sm text-gray-600 hover:text-gray-900 border border-gray-200 hover:border-gray-400 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                >
                  {loadFeedbackMutation.isPending ? "Loading…" : "Load Feedback"}
                </button>
                {feedbackData !== null && (
                  <span className="text-sm text-gray-500">
                    {feedbackData.length} record{feedbackData.length !== 1 ? "s" : ""}
                    {avgRating !== null && ` · avg rating ${avgRating}`}
                  </span>
                )}
              </div>

              {feedbackData !== null && feedbackData.length > 0 && (
                <div className="space-y-2">
                  <div className="max-h-36 overflow-y-auto space-y-1 border border-gray-100 rounded-lg p-2 bg-gray-50">
                    {feedbackData.map((r, i) => (
                      <div key={i} className="text-xs text-gray-600 flex gap-2">
                        <span className="shrink-0 font-medium text-gray-400">★{r.rating}</span>
                        <span>{r.comment || "(no comment)"}</span>
                      </div>
                    ))}
                  </div>

                  <button
                    onClick={() => improveMutation.mutate()}
                    disabled={improveMutation.isPending}
                    className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
                  >
                    <Sparkles size={13} />
                    {improveMutation.isPending ? "Improving…" : "Improve with Feedback"}
                  </button>
                </div>
              )}

              {improveMutation.isError && (
                <p className="text-xs text-red-500">{String(improveMutation.error)}</p>
              )}

              {improveResult && (
                <div className="border border-indigo-200 rounded-lg p-3 bg-indigo-50 space-y-2">
                  <p className="text-xs font-medium text-indigo-800">AI suggestions ready</p>
                  <pre className="text-xs text-gray-700 whitespace-pre-wrap max-h-32 overflow-y-auto font-mono">
                    {JSON.stringify(improveResult.config, null, 2)}
                  </pre>
                  <button
                    onClick={() => {
                      setForm((f) => applyGenerated(f, improveResult));
                      setImproveResult(null);
                    }}
                    className="text-sm bg-indigo-600 hover:bg-indigo-700 text-white font-medium px-3 py-1 rounded-lg transition-colors"
                  >
                    Apply Suggestions
                  </button>
                </div>
              )}
            </section>
          )}

          {saveMutation.isError && (
            <p className="text-sm text-red-500 px-1">{String(saveMutation.error)}</p>
          )}

          {!isNew && agent && (
            <section className="bg-white rounded-xl border border-gray-200 p-5 space-y-2">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400">Info</h2>
              <dl className="text-xs text-gray-400 space-y-1">
                <Row label="ID"><span className="font-mono">{agent.id}</span></Row>
                <Row label="Status">{agent.status}</Row>
                <Row label="Version">v{agent.version}</Row>
                <Row label="Created">{new Date(agent.created_at).toLocaleString()}</Row>
                <Row label="Updated">{new Date(agent.updated_at).toLocaleString()}</Row>
              </dl>
            </section>
          )}
        </div>
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

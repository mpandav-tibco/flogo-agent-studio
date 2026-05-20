import type { Agent, AgentCreate, AgentUpdate, DeployStatus, DockerDeployResult, DockerDeployStatus, FeedbackRecord, GeneratedConfig, Template } from "./types";

const BASE = "/api/v1/agents";
const TEMPLATES_URL = "/api/v1/templates";
const STORAGE_KEY = "forge_api_key";

function authHeader(): string {
  const key = localStorage.getItem(STORAGE_KEY) ?? "changeme";
  return "Basic " + btoa("flogo:" + key);
}

function headers(includeBody = false): HeadersInit {
  const h: HeadersInit = { Authorization: authHeader() };
  if (includeBody) h["Content-Type"] = "application/json";
  return h;
}

export function setApiKey(key: string) {
  if (key) localStorage.setItem(STORAGE_KEY, key);
  else localStorage.removeItem(STORAGE_KEY);
}

export function getApiKey(): string {
  return localStorage.getItem(STORAGE_KEY) ?? "changeme";
}

async function json<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    throw new Error("401: Unauthorized — check your API key in settings.");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

function normalizeAgent(a: unknown): Agent {
  const agent = a as Agent & { config: unknown };
  if (typeof agent.config === "string") {
    try {
      agent.config = JSON.parse(agent.config);
    } catch {
      agent.config = {};
    }
  }
  return agent as Agent;
}

// Design-service wraps all single/list responses in {records:[...]}
function unwrapOne<T>(data: { records: T[] } | T): T {
  if (data && typeof data === "object" && "records" in (data as object)) {
    return (data as { records: T[] }).records[0];
  }
  return data as T;
}

function unwrapMany<T>(data: { records: T[] } | T[]): T[] {
  if (data && !Array.isArray(data) && typeof data === "object" && "records" in (data as object)) {
    return (data as { records: T[] }).records ?? [];
  }
  return (data as T[]) ?? [];
}

function get(url: string): Promise<Response> {
  return fetch(url, { headers: headers() });
}

function mutate(url: string, method: string, body?: object): Promise<Response> {
  return fetch(url, {
    method,
    headers: headers(!!body),
    body: body ? JSON.stringify(body) : undefined,
  });
}

export const listAgents = (status?: string): Promise<Agent[]> =>
  get(status ? `${BASE}?status=${encodeURIComponent(status)}` : BASE)
    .then(json<{ records: Agent[] } | Agent[]>)
    .then((data) => unwrapMany<Agent>(data).map(normalizeAgent));

export const getAgent = (id: string): Promise<Agent> =>
  get(`${BASE}/${id}`)
    .then(json<{ records: Agent[] } | Agent>)
    .then((data) => normalizeAgent(unwrapOne<Agent>(data)));

export const createAgent = (body: AgentCreate): Promise<Agent> =>
  mutate(BASE, "POST", body)
    .then(json<{ records: Agent[] } | Agent>)
    .then((data) => normalizeAgent(unwrapOne<Agent>(data)));

export const updateAgent = (id: string, body: AgentUpdate): Promise<Agent> =>
  mutate(`${BASE}/${id}`, "PUT", body)
    .then(json<{ records: Agent[] } | Agent>)
    .then((data) => normalizeAgent(unwrapOne<Agent>(data)));

export const deleteAgent = (id: string): Promise<void> =>
  mutate(`${BASE}/${id}`, "DELETE").then((r) => {
    if (!r.ok && r.status !== 204) throw new Error(`${r.status}: ${r.statusText}`);
  });

export const purgeAgent = (id: string): Promise<void> =>
  mutate(`${BASE}/${id}/purge`, "DELETE").then((r) => {
    if (!r.ok && r.status !== 204) throw new Error(`${r.status}: ${r.statusText}`);
  });

export const cloneAgent = async (id: string): Promise<Agent> => {
  const source = await getAgent(id);
  return createAgent({
    name: `${source.name} (copy)`,
    description: source.description,
    config: { ...source.config },
  });
};

export const listTemplates = (): Promise<Template[]> =>
  get(TEMPLATES_URL).then(json<Template[]>);

// ── Deploy ────────────────────────────────────────────────────────────────────

export const deployAgent = (id: string): Promise<{ records: DeployStatus[] }> =>
  mutate(`${BASE}/${id}/deploy`, "POST", {}).then(json<{ records: DeployStatus[] }>);

export const undeployAgent = (id: string): Promise<{ records: DeployStatus[] }> =>
  mutate(`${BASE}/${id}/deploy`, "DELETE").then(json<{ records: DeployStatus[] }>);

export const getDeployStatus = (id: string): Promise<{ records: DeployStatus[] }> =>
  get(`${BASE}/${id}/deploy`).then(json<{ records: DeployStatus[] }>);

export const exportKubernetes = (id: string): Promise<string> =>
  get(`${BASE}/${id}/export/kubernetes`).then((r) => r.text());

export const exportDockerCompose = (id: string): Promise<string> =>
  get(`${BASE}/${id}/export/docker-compose`).then((r) => r.text());

// ── Docker Compose live deployment (via runtime manager :7050) ───────────────

const RUNTIME_BASE = "/api/runtime/agents";

export const dockerDeploy = (id: string): Promise<DockerDeployResult> =>
  fetch(`${RUNTIME_BASE}/${id}/docker-deploy`, { method: "POST", headers: headers(true) })
    .then(json<DockerDeployResult>);

export const dockerDeployStatus = (id: string): Promise<DockerDeployStatus> =>
  fetch(`${RUNTIME_BASE}/${id}/docker-deploy`, { method: "GET", headers: headers() })
    .then(json<DockerDeployStatus>);

export const dockerDeployStop = (id: string): Promise<DockerDeployResult> =>
  fetch(`${RUNTIME_BASE}/${id}/docker-deploy`, { method: "DELETE", headers: headers() })
    .then(json<DockerDeployResult>);

// ── Feedback ──────────────────────────────────────────────────────────────────

export const getAgentFeedback = async (agentId: string): Promise<FeedbackRecord[]> => {
  const res = await fetch(`/api/feedback/${agentId}`, { headers: headers() });
  if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`);
  const text = await res.text().then((t) => t.trim());
  if (!text) return [];
  // Service returns concatenated JSON objects with no delimiter: {}{}{} or NDJSON {}\n{}\n{}
  // Normalise both: replace any whitespace between } and { with a comma, then wrap in []
  try {
    return JSON.parse("[" + text.replace(/\}\s*\{/g, "},{") + "]") as FeedbackRecord[];
  } catch {
    return [];
  }
};

// ── Agent Builder ─────────────────────────────────────────────────────────────

export const generateAgentConfig = async (prompt: string, model?: string): Promise<GeneratedConfig> => {
  // Flow reads $flow.body.description (not prompt) — send both for safety.
  const raw = await mutate("/api/agent-builder/generate", "POST", { description: prompt, prompt, model })
    .then(json<GeneratedConfig>);

  // Flow returns { config: <flat LLM JSON>, description: "$flow.body.description" }.
  // The LLM schema uses "model" but the UI form uses "llmModel"; fix that here so
  // applyGenerated can populate the form without touching the Flogo service.
  if (raw?.config) {
    const cfg = raw.config as Record<string, unknown>;
    if (cfg["model"] && !cfg["llmModel"]) {
      cfg["llmModel"] = cfg["model"];
      delete cfg["model"];
    }
    // Hoist name / description up to the top level if absent there
    if (!raw.name && cfg["name"]) raw.name = cfg["name"] as string;
    if (!raw.description && cfg["description"]) raw.description = cfg["description"] as string;
  }
  return raw;
};

export const improveAgentConfig = (agentId: string, feedback: string): Promise<GeneratedConfig> =>
  mutate("/api/agent-builder/improve", "POST", { agentId, feedback }).then(json<GeneratedConfig>);

// ── Ingest ────────────────────────────────────────────────────────────────────

export const ingestDocuments = async (
  collection: string,
  documents: { text: string; source?: string }[],
  chunkStrategy?: string,
): Promise<string> => {
  const res = await mutate("/api/ingest", "POST", { collection, chunkStrategy, documents });
  const text = await res.text();
  if (!res.ok) throw new Error(text || res.statusText);
  return text;
};

export const ingestUrl = async (
  collection: string,
  url: string,
  chunkStrategy?: string,
): Promise<string> => {
  const res = await mutate("/api/ingest/url", "POST", { collection, url, chunkStrategy });
  const text = await res.text();
  if (!res.ok) throw new Error(text || res.statusText);
  return text;
};

export const ingestGitHub = async (
  collection: string,
  owner: string,
  repo: string,
  path: string,
  branch: string,
  chunkStrategy?: string,
): Promise<string> => {
  const res = await mutate("/api/ingest/github", "POST", {
    collection,
    owner,
    repo,
    path,
    branch: branch || "main",
    chunkStrategy,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(text || res.statusText);
  return text;
};

export const ingestConfluence = async (
  collection: string,
  spaceKey: string,
  baseUrl: string,
  chunkStrategy?: string,
): Promise<string> => {
  const res = await mutate("/api/ingest/confluence", "POST", { collection, spaceKey, baseUrl, chunkStrategy });
  const text = await res.text();
  if (!res.ok) throw new Error(text || res.statusText);
  return text;
};

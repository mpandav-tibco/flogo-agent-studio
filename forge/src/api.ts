import type { Agent, AgentCreate, AgentUpdate, DeployStatus, FeedbackRecord, GeneratedConfig, Template } from "./types";

const BASE = "/api/v1/agents";
const TEMPLATES_URL = "/api/v1/templates";
const STORAGE_KEY = "forge_api_key";

function authHeader(): string {
  const key = localStorage.getItem(STORAGE_KEY) ?? "changeme";
  return "Basic " + btoa(":" + key);
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
  get(status ? `${BASE}?status=${encodeURIComponent(status)}` : BASE).then(json<Agent[]>);

export const getAgent = (id: string): Promise<Agent> =>
  get(`${BASE}/${id}`).then(json<Agent>);

export const createAgent = (body: AgentCreate): Promise<Agent> =>
  mutate(BASE, "POST", body).then(json<Agent>);

export const updateAgent = (id: string, body: AgentUpdate): Promise<Agent> =>
  mutate(`${BASE}/${id}`, "PUT", body).then(json<Agent>);

export const deleteAgent = (id: string): Promise<void> =>
  mutate(`${BASE}/${id}`, "DELETE").then((r) => {
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

// ── Feedback ──────────────────────────────────────────────────────────────────

export const getAgentFeedback = async (agentId: string): Promise<FeedbackRecord[]> => {
  const res = await fetch(`/api/feedback/${agentId}`, { headers: headers() });
  if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`);
  const text = await res.text();
  return text
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line) as FeedbackRecord);
};

// ── Agent Builder ─────────────────────────────────────────────────────────────

export const generateAgentConfig = (prompt: string, model?: string): Promise<GeneratedConfig> =>
  mutate("/api/agent-builder/generate", "POST", { prompt, model }).then(json<GeneratedConfig>);

export const improveAgentConfig = (agentId: string, feedback: string): Promise<GeneratedConfig> =>
  mutate("/api/agent-builder/improve", "POST", { agentId, feedback }).then(json<GeneratedConfig>);

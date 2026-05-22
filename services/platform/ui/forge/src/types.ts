export interface AgentConfig {
  systemPrompt?: string;
  collectionName?: string;
  topK?: number;
  llmProvider?: string;
  llmModel?: string;
  llmBaseUrl?: string;
  temperature?: number;
  chunkStrategy?: string;
  embeddingProvider?: string;
  embeddingModel?: string;
  embeddingBaseUrl?: string;
  // Populated by deployment.py once the agent runtime is running
  chatUiUrl?: string;
  chatApiUrl?: string;
  sseUrl?: string;
  ingestionUrl?: string;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  status: "draft" | "active" | "archived";
  config: AgentConfig;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface Template {
  id: string;
  name: string;
  description: string;
  config: AgentConfig;
}

export type AgentCreate = {
  name: string;
  description: string;
  config: AgentConfig;
};

export type AgentUpdate = Partial<AgentCreate> & { status?: Agent["status"] };

export interface DeployStatus {
  id: string;
  agentId: string;
  status: "draft" | "active" | "archived";
  version: number;
  deployedAt?: string;
}

export interface FeedbackRecord {
  agentId: string;
  sessionId: string;
  messageId?: string;
  rating: number | string;
  comment: string;
}

export interface FeedbackSummary {
  total: number;
  avgRating: number;
  records: FeedbackRecord[];
}

export interface GeneratedConfig {
  name?: string;
  description?: string;
  config: AgentConfig & {
    systemPrompt?: string;
    active?: boolean;
  };
}

export interface DockerDeployResult {
  success: boolean;
  agentId?: string;
  composeFile?: string;
  stdout?: string;
  stderr?: string;
  exitCode?: number;
  error?: string;
}

export interface DockerDeployStatus {
  status: "running" | "stopped" | "not_deployed";
  agentId?: string;
  composeFile?: string;
  containers: Array<{ Name: string; State: string; Status: string }>;
}

/** Runtime state record returned by GET /api/runtime/agents/{agentId} */
export interface AgentRuntime {
  agentId: string;
  agentName: string;
  slot: number;
  ports: Record<string, number>;
  pids: Record<string, number | null>;
  chatUiUrl: string;
  chatApiUrl: string;
  sseUrl: string;
  ingestionUrl: string;
  startedAt: number;
  readiness: "starting" | "ready" | "degraded";
  health: Record<string, "running" | "dead">;
}

/** One platform service entry returned by GET /api/admin/services */
export interface PlatformService {
  name: string;
  port: number;
  status: "online" | "offline";
  pid: number | null;
  category: "platform" | "agent-support";
}

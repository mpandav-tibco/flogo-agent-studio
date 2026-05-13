export interface AgentConfig {
  systemPrompt?: string;
  collectionName?: string;
  topK?: number;
  llmProvider?: string;
  llmModel?: string;
  llmBaseUrl?: string;
  temperature?: number;
  chunkStrategy?: string;
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
  rating: number | string;
  comment: string;
  timestamp?: string;
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

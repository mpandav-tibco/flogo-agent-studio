-- Flogents Studio — PostgreSQL schema init
-- Runs on first start via /docker-entrypoint-initdb.d/

-- ── Agent registry (design-service / platform-service) ────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name        TEXT        NOT NULL,
    description TEXT,
    status      TEXT        DEFAULT 'draft',
    config      JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    version     INTEGER     DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_agents_status     ON agents (status);
CREATE INDEX IF NOT EXISTS idx_agents_created_at ON agents (created_at DESC);

-- ── Chat history — persistent conversation turns per session ──────────────────
-- session_id  : matches sessionId sent by UI / API callers
-- agent_id    : which agent handled the turn (for multi-agent history queries)
-- role        : 'user' | 'assistant'
-- content     : message text (user message or LLM answer)
-- metadata    : JSON bag — source_documents, duration_ms, tokens, etc.
CREATE TABLE IF NOT EXISTS chat_history (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    agent_id    TEXT        NOT NULL,
    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT        NOT NULL,
    metadata    JSONB       DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_history_session  ON chat_history (session_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_chat_history_agent    ON chat_history (agent_id, created_at DESC);

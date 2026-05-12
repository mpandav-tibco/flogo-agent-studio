# Flogo Agent Studio

Welcome to **Flogo Agent Studio** — a multi-agent AI platform built on TIBCO Flogo.

### Getting started

1. **Select an agent** using the buttons that appear below this message.
2. **Ask a question** — the agent retrieves relevant knowledge and generates an answer.
3. **Rate responses** with 👍 or 👎 to help improve future answers.

### Available services

| Service | Port | Purpose |
|---------|------|---------|
| Agent Chat | 7001 | RAG retrieval + LLM answer generation |
| Config | 7004 | Multi-agent registry |
| Feedback | 7003 | Response ratings |
| Rule Engine | 7000 | Flogo app analysis |
| Ingestion | 7002 | Knowledge base loading |
| MCP Server | 3333 | Claude/Cursor tool integration |

### Tips

- Different agents are specialized for different knowledge domains.
- The `flogo-analyzer` agent can review `.flogo` application files.
- Switch agents mid-conversation using the selector buttons.

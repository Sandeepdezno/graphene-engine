# Graphene Engine — Project Constitution

## What we are building
A headless **SAP knowledge engine**. It connects to any SAP system, learns its structure and data,
and lets any LLM or agent answer natural-language business questions about it
(e.g. "show this month's sales", "what happened on 12 October?").

This is an investor demo. It must prove the idea clearly and reliably, not handle production scale.

**Core pitch:** the LLM needs no SAP training — the engine teaches it the system.

## Architecture (four layers — keep them separate)
1. **SAP Connector** (`sap_connector/`) — talks OData (RFC later) to SAP. System-agnostic:
   switching from the sandbox to a real S/4HANA system is a config change only.
2. **Knowledge Layer** (`knowledge/`) — Neo4j schema graph built automatically from SAP
   `$metadata`, plus live query tools that turn a structured intent into an OData query.
3. **MCP Tool Server** (`mcp_server/`) — exposes engine capabilities as standard MCP tools
   so any LLM/agent can plug in.
4. **Agent** (`agent/`) — local Llama via Ollama with a simple chat interface.

Each layer only talks to the layer directly below it. The agent never calls SAP directly.

## Non-negotiable rules
- **Read-only against SAP.** No POST/PATCH/PUT/DELETE calls to any SAP system, ever.
- **No hardcoded SAP knowledge.** Entity names, fields and relationships come from the
  system's metadata, never from code. Must work on a system we've never seen.
- **Answers come from SAP data, not the LLM's imagination.** Every factual answer must be
  backed by a tool call; if data isn't available, say so.
- **Secrets live in `.env` only.** `.env` is git-ignored. Never log, print or commit API keys,
  passwords or tokens. Keep `.env.example` up to date.
- **Config-driven targets.** Sandbox vs real system, and the LLM model, are set via
  environment variables (`SAP_TARGET`, `OLLAMA_MODEL` — `llama3.2:3b` dev, `llama3.1:8b` demo).
- **Runs fully local** apart from the SAP connection. No cloud LLM dependency for the demo.
- **Must run on Windows** (the demo machine). Use `pathlib`, avoid shell-specific scripts,
  and keep dependencies pip-installable.
- **Offline tests.** pytest suites use recorded SAP responses so they run without network.

## Targets
- **Dev:** SAP Business Accelerator Hub sandbox (S/4HANA Cloud APIs, header `APIKey`).
- **Demo:** partner's S/4HANA demo system (OData + RFC enabled; read-only user to be confirmed).

## Reference project
The previous Graphene project sits next to this folder. Treat it as **read-only reference**.
Reuse ideas from: Ollama streaming, intent classification, Neo4j setup, Windows migration kit.
Do **not** copy the Excel import, graph-rendering GUI or impact-analysis UI.

## Working style
- Sprint-based. Plan first, then implement. State the "done when" check and verify it.
- Small, readable Python. Type hints. Clear error messages when SAP is unreachable.
- Ask before adding a new major dependency.

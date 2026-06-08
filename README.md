<div align="center">

# Self-Evolution Data Agent

**A self-evolving, knowledge-driven NL2SQL Agent for multi-source databases — ask your data in plain language, get charts back.**

[English](./README.md) · [中文](./README_CN.md)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Vite-61DAFB?logo=react&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-supported-4479A1?logo=mysql&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-supported-47A248?logo=mongodb&logoColor=white)
![LLM](https://img.shields.io/badge/LLM-Qwen%20%7C%20Claude%20%7C%20GPT%20%7C%20DeepSeek-2DD4BF)
![License](https://img.shields.io/badge/License-Apache%202.0-D22128?logo=apache&logoColor=white)

</div>

---

## What is Self-Evolution Data Agent?

**Self-Evolution Data Agent is an open-source NL2SQL agent that turns a plain-language question into a real database query and renders the answer as a chart or table.** A business user types one sentence in natural language; the agent reasons over your schema, runs the query against MySQL or MongoDB, and returns a visualization — no SQL, no BI team, no pre-built data model required.

Unlike a traditional **ChatBI** dashboard or a single-shot **text-to-SQL** translator, Self-Evolution Data Agent is built as an **Agent Loop**: the LLM actively explores your schema, calls tools, validates its own work across multiple reasoning–execution–observation rounds, asks for clarification when it is stuck, and **distills every clarification back into a growing knowledge base**. The more you use it, the more accurate it gets.

### The problem it solves

Most organizations sit on data that only engineers can query. Analysts wait days for a SQL request; BI teams spend weeks pre-modeling dashboards that go stale. Existing NL2SQL tools help, but they break the moment a question falls outside their hand-written examples, and they forget everything between sessions.

Self-Evolution Data Agent removes those bottlenecks:

- **No SQL skills needed** — business users self-serve data by asking in natural language.
- **No BI team, no pre-modeling** — the agent learns your schema and business terms automatically from your code repositories.
- **No vendor lock-in on the model** — swap between Qwen, GPT, DeepSeek, Claude, or a local vLLM / Ollama endpoint by changing one environment variable.
- **Zero intrusion on production** — read-only connections, no schema changes, no plugins, no touching your business code.

---

## Why it beats traditional ChatBI and text-to-SQL

Most NL2SQL products treat the LLM as a passive translation machine: you feed it knowledge, it spits out one SQL string, and if it is wrong you start over. Self-Evolution Data Agent hands the initiative to the LLM and lets it think, recover, and learn.

| Dimension | Traditional ChatBI / text-to-SQL | Self-Evolution Data Agent |
|---|---|---|
| **Who's in control** | LLM passively translates the question into a single SQL string | LLM drives an **Agent Loop**: autonomously probes schema, calls tools, runs multi-round reasoning → execution → observation |
| **When knowledge is missing** | Hits a wall — fabricates wrong SQL or gives up; capability = knowledge boundary | Freely explores and tries; if truly stuck, **proactively asks the user to clarify** instead of failing |
| **Getting better over time** | Clarifications are discarded; the same question stalls again next time | Clarifications are **distilled into knowledge and fed back** — it gets smarter with every question (self-evolving) |
| **Complex / correlated queries** | Crams everything into one giant SQL statement — heavy multi-join / full-table scans risk **blowing up the database** | Generates an **execution plan** and runs it in **decomposed steps**, passing intermediate results between steps and aggregating at the end — no single monster query |
| **Error handling** | Returns failure, user must rephrase | Error-class sliding window → active clarification; **pauses instead of aborting** |
| **Knowledge source** | Hand-written few-shot examples | Multi-channel: **Git source parsing** (cold start) + manual entry + conversation distillation |
| **Memory** | None, starts from zero each time | **A-MEM** memory evolution + recency / adoption-rate decay |
| **Databases** | Usually locked to one SQL dialect | **MySQL + MongoDB** unified under one agent loop; pluggable drivers |
| **Observability** | Black box, not interruptible | End-to-end **SSE streaming** + Langfuse tracing + real-time steering / cancel |

### Four pillars

1. **Agent Loop, not a translator.** A multi-round reasoning-and-acting loop over a toolset replaces single-prompt generation, with quota buckets, dead-loop detection, and forced clarification on error classes as safety nets. For complex correlated questions it builds an **execution plan** and runs it in **decomposed steps** — passing intermediate results between steps and aggregating at the end, instead of firing one monster join that can blow up the database.
2. **Out-of-the-box, built for SMBs.** No BI team, no self-hosted model, zero intrusion on production. Cold-start your schema knowledge automatically from a Git repository and start asking on day one.
3. **A knowledge layer as a moat.** Knowledge is not static few-shot — it's a living asset that auto-bootstraps, is human-reviewed, and self-evolves. Includes a `proposed → canonical` review state machine, A-MEM memory evolution, and HyQE multi-vector recall.
4. **Multi-source, one interaction.** Relational and document databases both run through the same agent loop. `db_type` flows through the whole stack, so adding a new database requires zero changes to the upper layers.

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL 16 (metadata store)
- An LLM API key (Qwen / DashScope by default, or any OpenAI-compatible endpoint)

### 1. Configure environment

Copy the example env file and fill in your LLM credentials:

```bash
cp .env.example .env
```

```dotenv
# LLM provider: qwen | claude
IS_LLM_PROVIDER=qwen
IS_LLM_API_KEY=your-dashscope-api-key
IS_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
IS_LLM_MODEL=qwen-plus

# Optional: Git token for cloning private repos (GitHub / GitLab / Gitee)
IS_GIT_TOKEN=
```

> **Tip:** `IS_LLM_BASE_URL` accepts any OpenAI-compatible endpoint, so GPT, DeepSeek, or a local vLLM / Ollama server work by changing this one line.

### 2. Start PostgreSQL (metadata database)

```bash
docker run -d --name pg-is -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
docker exec -it pg-is psql -U postgres -c "CREATE DATABASE intelligent_statistics;"
docker exec -it pg-is psql -U postgres -c "CREATE DATABASE intelligent_statistics_test;"
```

The connection string is configured via `IS_METADATA_DB_URL` (defaults to `postgresql+asyncpg://postgres:postgres@localhost:5432/intelligent_statistics`).

### 3. Run the backend (FastAPI + uvicorn)

```bash
cd backend && pip install .
make dev-backend          # uvicorn app.main:app --port 8001
```

Verify it's up: open <http://localhost:8001/docs>.

### 4. Run the frontend (React + Vite)

```bash
cd frontend && npm install
make dev-frontend         # vite dev server on port 3000
```

Open <http://localhost:3000> and start asking questions.

### Run with Docker

```bash
make build && make up     # docker compose build && up -d
make down                 # stop
```

The Docker frontend is served on port `3000` and the backend on port `8000`. Set the `IS_*` variables in your shell or `.env` before `make up`.

### First query in 4 steps

1. Create a namespace and register a MySQL or MongoDB datasource.
2. Add a Git repository and trigger training — the agent parses your schema and business terms automatically.
3. Wait for schema candidates to aggregate, then review and confirm them in the UI.
4. Ask a question in natural language and get a chart back.

---

## Architecture at a glance

```
Natural language → LLM (Agent Loop + tool calls) → DB execution → chart / table
```

| Layer | Responsibility |
|---|---|
| `engine/llm.py` | Unified LLM entry point; routes between Qwen / Claude (and any OpenAI-compatible endpoint) |
| `engine/executor.py` | Full query flow: load datasource → clarify → route engine → safety check → execute → visualize → persist |
| `engine/agent_loop` | Multi-round reasoning-and-acting loop with tool calls, quota buckets, and dead-loop detection |
| `drivers/mysql.py` | MySQL driver (aiomysql + INFORMATION_SCHEMA introspection) |
| `drivers/mongo.py` | MongoDB driver (Motor async + Flavor detection for DocumentDB differences) |
| `knowledge/` | Git parsing, `proposed → canonical` review state machine, retrieval, HyQE, A-MEM, decay |

### Tech stack

| Area | Stack |
|---|---|
| **Frontend** | React + Vite · SSE streaming client · Vitest / Playwright |
| **Backend** | Python 3.11 + FastAPI + uvicorn · asyncio agent loop |
| **LLM** | Qwen / Claude dual provider · unified `chat_completion` routing · OpenAI-compatible endpoints |
| **Retrieval** | ChromaDB vector store · Chinese embeddings · layered recall |
| **Metadata** | PostgreSQL 16 · namespaces / knowledge / agent traces |
| **Execution** | Unified agent loop · MySQL (aiomysql) / MongoDB (Motor) drivers |

### Safety guardrails

- **MySQL:** `SELECT`-only, automatic `LIMIT`, `EXPLAIN` row-count pre-check (blocks > 500K rows, warns > 100K).
- **MongoDB:** operation allowlist (`find` / `aggregate` / `count_documents`), forced `$limit`.
- **Agent:** quota buckets + dead-loop detection + forced clarification on error classes + cancellation safety nets.

---

## FAQ

**How is this different from a text-to-SQL library?**
A text-to-SQL library translates one prompt into one SQL string. Self-Evolution Data Agent runs an agent loop that probes the schema, calls tools, validates results, recovers from errors, asks for clarification, and learns from each session.

**Do I need to write SQL?**
No. Business users ask in natural language. The agent generates and executes the query for MySQL or MongoDB.

**Which LLMs are supported?**
Qwen and Claude out of the box. Any OpenAI-compatible endpoint (GPT, DeepSeek, local vLLM / Ollama) works by setting `IS_LLM_BASE_URL` and `IS_LLM_PROVIDER`.

**Is my production database safe?**
Connections are read-only. The agent never alters table structures, installs plugins, or touches business code. SQL is `SELECT`-only with enforced row limits and pre-execution row-count checks.

**Does it support databases other than MySQL and MongoDB?**
MySQL and MongoDB ship today. The driver layer is pluggable — `db_type` flows through the entire stack, so PostgreSQL, ClickHouse, and others reuse the same upper-layer pipeline.

---

## License

Licensed under the [Apache License 2.0](LICENSE).

---

## Keywords

NL2SQL · Text-to-SQL · ChatBI · natural language to SQL · LLM Agent · Agent Loop · ReAct · RAG · self-evolving knowledge base · MySQL · MongoDB · data visualization · Qwen · Claude · GPT · DeepSeek

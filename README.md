# ambient-expense-agent

An **ambient expense-approval agent** that runs as an event-driven web service. Instead of requiring a human to open a chat UI and type a message, the agent is triggered by Pub/Sub push messages — it receives expense reports, routes them through a security pipeline, and pauses for human approval only when needed.

Built with [Google ADK](https://adk.dev/) (Agent Development Kit) and [FastAPI](https://fastapi.tiangolo.com/).

## Architecture

```
Pub/Sub push message
       │
       ▼
 ┌─────────────────┐
 │  FastAPI server  │  port 8080 (make serve)
 │  POST /push      │
 └────────┬────────┘
          │  base64-decode + normalize subscription → userId
          ▼
 ┌─────────────────────────────────────────────────┐
 │              ADK Workflow                        │
 │                                                  │
 │  parse_event → route_expense ──┬→ auto_approver  │  (amount < $100)
 │                                │                  │
 │                                └→ security_check  │  (amount ≥ $100)
 │                                      │            │
 │                          ┌───────────┴──────────┐ │
 │                          ▼                      ▼ │
 │                     clean               security_event
 │                          │                (injection/PII)
 │                          ▼                      │
 │                   llm_reviewer                  │
 │                          │                      │
 │                          └──────────┬───────────┘
 │                                     ▼            │
 │                            human_approval        │
 │                            (pauses for human)    │
 └─────────────────────────────────────────────────┘
```

### Workflow nodes

| Node | Purpose |
|------|---------|
| `parse_event` | Decodes the Pub/Sub payload into a validated `ExpenseReport` |
| `route_expense` | Routes to `auto_approve` if amount < $100, otherwise `security_check` |
| `security_checkpoint` | Scrubs PII (SSNs, credit cards), detects prompt-injection phrases → routes to `clean` or `security_event` |
| `auto_approver` | Auto-approves low-value expenses (no LLM call) |
| `llm_reviewer` | Gemini LLM assesses risk level and recommends approve/reject |
| `human_approval` | Pauses for human input (interactive approval or security event review) |

### Security features

- **PII scrubbing**: SSNs and credit-card numbers are redacted to `[REDACTED_SSN]` / `[REDACTED_CC]` before any downstream node sees them
- **Prompt-injection detection**: Known injection phrases (`"ignore previous instructions"`, `"bypass"`, `"auto-approve"`, etc.) route directly to `human_approval` as a security event — the LLM reviewer never runs
- **Amount threshold**: Expenses ≥ $100 require LLM review + human approval; expenses < $100 are auto-approved

## Project Structure

```text
ambient-expense-agent/
├── expense_agent/             # Core agent code
│   ├── agent.py               # ADK workflow definition (nodes + routing)
│   ├── config.py              # Thresholds and model settings
│   ├── models.py              # Pydantic data models (ExpenseReport, RiskAssessment)
│   └── server.py              # FastAPI Pub/Sub webhook server (port 8080)
├── tests/
│   ├── unit/                  # Fast, isolated tests
│   │   ├── test_nodes.py      # Workflow node business logic
│   │   └── test_server_helpers.py  # Server helper functions
│   └── integration/           # End-to-end tests
│       ├── test_agent.py      # Full workflow integration tests
│       └── test_server.py     # FastAPI endpoint tests (TestClient)
├── postman/
│   └── ambient-expense-agent.postman_collection.json  # Postman collection
├── GEMINI.md                  # AI-assisted development guide
├── Makefile                   # Shortcuts for test, lint, serve, playground
└── pyproject.toml             # Project dependencies and tool config
```

## Requirements

Before you begin, ensure you have:

- **Python 3.12** (recommended): this project pins `3.12.3` via `.python-version` for [pyenv](https://github.com/pyenv/pyenv). Install with `pyenv install 3.12.3` if needed.
- **uv**: Python package manager (used for all dependency management in this project) - [Install](https://docs.astral.sh/uv/getting-started/installation/) ([add packages](https://docs.astral.sh/uv/concepts/dependencies/) with `uv add <package>`)
- **agents-cli**: Agents CLI - Install with `uv tool install google-agents-cli`
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)
- **GEMINI_API_KEY**: Set in `.env` for LLM review functionality (expenses ≥ $100). Without it, the auto-approve and security paths still work; only the LLM review test is skipped.

> **Important:** Always run tests and linters through `uv run ...` or the `Makefile` targets below. That ensures you use this project's virtual environment (`.venv`) and installed tools — not a different Python version from your system or pyenv global.

## Quick Start

Install `agents-cli` and its skills if not already installed:

```bash
uvx google-agents-cli setup
```

Install required packages:

```bash
make install
```

### Two ways to run the agent

This project has **two separate servers** — start either or both depending on your needs:

| Server | Command | Port | Purpose |
|--------|---------|------|---------|
| **Webhook** | `make serve` | 8080 | Pub/Sub push endpoint — the ambient agent. Receives expense payloads, runs the workflow automatically. |
| **Playground** | `make playground` | 8000 | ADK Web UI — interactive chat interface with dev-ui for session inspection. |

**Start the ambient webhook server:**

```bash
make serve
# → Uvicorn running on http://0.0.0.0:8080
```

**Start the interactive playground (separate terminal):**

```bash
make playground
# → ADK Web UI on http://127.0.0.1:8000
```

> **Important:** Each server maintains its own separate in-memory session store. Sessions created via `/push` on port 8080 will NOT appear in the playground's dev-ui on port 8000, and vice versa. Use the [session inspection API](#session-inspection) to inspect webhook sessions.

## Commands

| Command | Description |
|---------|-------------|
| `make install` | Install dependencies + git hooks (uv sync --group dev) |
| `make serve` | Start the Pub/Sub webhook server (port 8080) |
| `make playground` | Start the ADK interactive playground (port 8000) |
| `make test` | Run unit and integration tests (49 tests) |
| `make lint` | Run ruff + pylint |
| `make lint-fix` | Auto-fix ruff issues + format |
| `agents-cli install` | Install dependencies using uv |
| `agents-cli playground` | Launch local development environment (same as `make playground`) |
| `agents-cli lint` | Run code quality checks |
| `agents-cli eval` | Evaluate agent behavior (see `agents-cli eval --help`) |
| `agents-cli deploy` | Deploy agent to Agent Runtime |
| `agents-cli publish gemini-enterprise` | Register deployed agent to Gemini Enterprise |

## API Reference

### Webhook server (port 8080)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/push` | Pub/Sub push endpoint — receives expense payloads and runs the workflow |
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |
| `GET` | `/sessions` | List all sessions grouped by user (discover userIds) |
| `GET` | `/sessions?user_id=<id>` | List sessions for a specific user |
| `GET` | `/sessions/{session_id}?user_id=<id>` | Get full session details including all events |

### Playground server (port 8000)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/dev-ui/` | Interactive chat UI — open in browser |
| `GET` | `/list-apps` | Lists registered agents |

### POST /push — Pub/Sub envelope

The `/push` endpoint accepts the standard Pub/Sub push envelope:

```json
{
  "message": {
    "data": "<base64-encoded JSON>",
    "messageId": "msg-001",
    "attributes": {"source": "postman"}
  },
  "subscription": "projects/my-project/subscriptions/expense-approvals"
}
```

The `subscription` field is a fully-qualified Pub/Sub path. The server normalizes it to the short name (`expense-approvals`) which becomes the `userId` for the ADK session — keeping session records readable.

The `message.data` field is base64-encoded JSON containing the expense payload:

```json
{
  "amount": 150.0,
  "submitter": "alice@company.com",
  "category": "software",
  "description": "IDE License",
  "date": "2026-06-06"
}
```

### Sending a test message

```bash
curl -s -X POST http://127.0.0.1:8080/push \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "data": "eyJhbW91bnQiOiAxNTAuMCwgInN1Ym1pdHRlciI6ICJhbGljZUBjb21wYW55LmNvbSIsICJjYXRlZ29yeSI6ICJzb2Z0d2FyZSIsICJkZXNjcmlwdGlvbiI6ICJJREUgTGljZW5zZSIsICJkYXRlIjogIjIwMjYtMDYtMDYifQ==",
      "messageId": "msg-001"
    },
    "subscription": "projects/my-project/subscriptions/expense-approvals"
  }' | python3 -m json.tool
```

To generate the base64 for a different payload:

```bash
echo -n '{"amount":150.0,"submitter":"alice@company.com","category":"software","description":"IDE License","date":"2026-06-06"}' | base64
```

### Session inspection

After sending a `/push` request, use the session endpoints to discover the `userId` and inspect session details:

```bash
# List all sessions — discover userIds and session IDs
curl -s http://127.0.0.1:8080/sessions | python3 -m json.tool

# List sessions for a specific user
curl -s "http://127.0.0.1:8080/sessions?user_id=expense-approvals" | python3 -m json.tool

# Get full session details with all events
curl -s "http://127.0.0.1:8080/sessions/<session-id>?user_id=expense-approvals" | python3 -m json.tool
```

The `userId` is the normalized subscription name (e.g. `expense-approvals`). To inspect sessions in the browser dev-ui, open:

```
http://127.0.0.1:8000/dev-ui/?app=expense_agent&userId=expense-approvals
```

> **Note:** The dev-ui is served by the playground (port 8000) which has a separate session store. Sessions created via `/push` on port 8080 are only visible via the API endpoints above, not in the dev-ui.

## Postman Collection

A Postman collection is included for testing all scenarios:

```
postman/ambient-expense-agent.postman_collection.json
```

**Import it in Postman:** File → Import → select the JSON file.

The collection includes **16 requests across 9 folders**:

| Folder | Requests | What it tests |
|--------|----------|---------------|
| **Health** | 2 | Liveness checks for both servers (8080 + 8000) |
| **Auto-Approve** | 1 | $25 expense → auto-approved (no LLM) |
| **LLM Review** | 1 | $750 expense → security check → LLM review → human approval |
| **PII Scrubbing** | 1 | SSN + credit card in description → redacted before downstream |
| **Prompt Injection** | 1 | "Ignore previous instructions" → security event (no LLM) |
| **Malicious Combo** | 1 | $1M + injection + SSN → security event + PII scrubbed |
| **Boundary Cases** | 2 | Exactly $100 and $100.01 — threshold edge cases |
| **Error Cases** | 3 | Invalid JSON, missing data, invalid base64 → 400s |
| **Sessions** | 4 | List sessions, get session by ID, open dev-ui |

**Collection variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `base_url` | `http://127.0.0.1:8080` | Webhook server URL |
| `playground_url` | `http://127.0.0.1:8000` | Playground/dev-ui URL |
| `subscription_path` | `projects/my-project/subscriptions/expense-approvals` | Pub/Sub subscription path |
| `user_id` | `expense-approvals` | Auto-set by session listing test scripts |
| `session_id` | (empty) | Auto-set by session listing test scripts |

The "List all sessions" request includes a **test script** that auto-extracts `user_id` and `session_id` from the response, so subsequent requests work without manual copy-paste.

---

## Testing

### One-time setup

Install project dependencies **including dev tools** (pytest, ruff, pylint):

```bash
make install
# or: uv sync --group dev
```

### Run all tests

```bash
make test
```

Equivalent command:

```bash
uv run python -m pytest tests/unit tests/integration
```

### Run a single test file

```bash
uv run python -m pytest tests/integration/test_server.py
```

### Run one specific test

```bash
uv run python -m pytest tests/integration/test_agent.py::test_auto_approve_low_amount
```

### Test breakdown

| Suite | File | Tests | What it covers |
|-------|------|-------|----------------|
| Unit | `test_nodes.py` | 29 | Workflow node business logic (parse, route, security, auto-approve) |
| Unit | `test_server_helpers.py` | 6 | `normalize_subscription()` helper function |
| Integration | `test_agent.py` | 3 | Full workflow end-to-end (auto-approve, PII scrubbing, LLM review) |
| Integration | `test_server.py` | 11 | FastAPI endpoints (health, push, errors, sessions, all scenarios) |
| **Total** | | **49** | |

### LLM-dependent tests

Tests that require live Gemini API calls are marked with `@pytest.mark.llm` and auto-skipped when no `GEMINI_API_KEY` or `GOOGLE_CLOUD_PROJECT` environment variable is set. The auto-approve and security paths do not call the LLM, so they always run.

### What to expect

- **49 tests** should pass (35 unit + 14 integration).
- Zero warnings (third-party deprecation warnings are filtered in `pyproject.toml`).

---

## Linting

This project uses two complementary tools:

| Tool | Role | Auto-fix? |
|------|------|-----------|
| **ruff** | Fast style checks (imports, formatting, common bugs) | Yes |
| **pylint** | Deeper code-quality checks (design, naming, logic smells) | No — reports issues for you to fix manually |

### Run all linters

```bash
make lint
```

Equivalent commands:

```bash
uv run ruff check expense_agent tests
uv run python -m pylint expense_agent tests
```

### Auto-fix (ruff only)

Ruff can automatically fix many issues (import order, simple style problems) and reformat files:

```bash
make lint-fix
```

Equivalent commands:

```bash
uv run ruff check --fix expense_agent tests
uv run ruff format expense_agent tests
```

After auto-fix, run `make lint` again. Pylint may still flag items that need a manual edit (for example, renaming a variable or simplifying logic).

### Suggested workflow before committing

```bash
make lint-fix   # auto-fix what ruff can
make lint       # confirm ruff + pylint are clean
make test       # confirm tests still pass
```

### Pre-commit hook (automatic)

This repo includes a git hook that runs **lint + tests** before every commit. Enable it once:

```bash
make install-hooks
```

`make install` runs this automatically. On each `git commit`, the hook runs:

1. `make lint` (ruff + pylint)
2. `make test`

If either step fails, the commit is blocked until you fix the issues.

To skip the hook in an emergency (not recommended):

```bash
git commit --no-verify -m "your message"
```

---

## Development

### Editing the agent

Edit workflow logic in `expense_agent/agent.py`, data models in `expense_agent/models.py`, and configuration thresholds in `expense_agent/config.py`.

### Editing the webhook server

Edit `expense_agent/server.py` to change endpoint behavior, add new routes, or modify subscription normalization.

### Hot reload

The playground (`make playground`) auto-reloads on save. The webhook server (`make serve`) does not — restart it after changes:

```bash
# Kill and restart
lsof -ti:8080 | xargs kill -9 2>/dev/null; make serve
```

### Restarting both servers

If both are running and you've changed code:

```bash
lsof -ti:8080 | xargs kill -9 2>/dev/null
lsof -ti:8000 | xargs kill -9 2>/dev/null
make serve        # terminal 1 — webhook on port 8080
make playground   # terminal 2 — dev-ui on port 8000
```

## Configuration

| Setting | Location | Default | Description |
|---------|----------|---------|-------------|
| `THRESHOLD_USD` | `expense_agent/config.py` | `100.0` | Expenses ≥ this amount go through security check + LLM review |
| `REVIEW_MODEL` | `expense_agent/config.py` | `gemini-3.1-flash-lite` | Gemini model used for LLM review |
| `INJECTION_KEYWORDS` | `expense_agent/agent.py` | `["ignore previous", "bypass", "auto-approve", ...]` | Phrases that trigger security event routing |
| Webhook port | `expense_agent/server.py` | `8080` | Port for the FastAPI webhook server |
| Playground port | `Makefile` | `8000` | Port for the ADK playground |
| Telemetry | `expense_agent/server.py` | `otel_to_cloud=False` | OpenTelemetry is local-only (no Cloud Trace export) |

## Deployment

```bash
gcloud config set project <your-project-id>
agents-cli deploy
```

To add CI/CD and Terraform, run `agents-cli scaffold enhance`.
To set up your production infrastructure, run `agents-cli infra cicd`.

## Observability

- **Local development**: Standard Python logging to console (`INFO` level). OpenTelemetry is configured with `otel_to_cloud=False` — traces stay local, no Cloud Trace export.
- **Production**: Built-in telemetry exports to Cloud Trace, BigQuery, and Cloud Logging when deployed via `agents-cli deploy`.

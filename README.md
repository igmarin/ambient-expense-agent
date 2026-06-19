# ambient-expense-agent

Simple ReAct agent
Agent generated with `agents-cli` version `0.5.0`

## Project Structure

```text
ambient-expense-agent/
├── expense_agent/             # Core agent code
│   ├── agent.py               # Main workflow logic
│   ├── config.py              # Thresholds and model settings
│   └── models.py              # Pydantic data models
├── tests/
│   ├── unit/                  # Fast, isolated tests
│   └── integration/           # End-to-end agent workflow tests
├── GEMINI.md                  # AI-assisted development guide
├── Makefile                   # Shortcuts for test and lint commands
└── pyproject.toml             # Project dependencies and tool config
```

> 💡 **Tip:** Use [Gemini CLI](https://github.com/google-gemini/gemini-cli) for AI-assisted development - project context is pre-configured in `GEMINI.md`.

## Requirements

Before you begin, ensure you have:

- **Python 3.12** (recommended): this project pins `3.12.3` via `.python-version` for [pyenv](https://github.com/pyenv/pyenv). Install with `pyenv install 3.12.3` if needed.
- **uv**: Python package manager (used for all dependency management in this project) - [Install](https://docs.astral.sh/uv/getting-started/installation/) ([add packages](https://docs.astral.sh/uv/concepts/dependencies/) with `uv add <package>`)
- **agents-cli**: Agents CLI - Install with `uv tool install google-agents-cli`
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)

> **Important:** Always run tests and linters through `uv run ...` or the `Makefile` targets below. That ensures you use this project's virtual environment (`.venv`) and installed tools — not a different Python version from your system or pyenv global.

## Quick Start

Install `agents-cli` and its skills if not already installed:

```bash
uvx google-agents-cli setup
```

Install required packages:

```bash
agents-cli install
```

Test the agent with a local web server:

```bash
agents-cli playground
```

You can also use features from the [ADK](https://adk.dev/) CLI with `uv run adk`.

## Commands

| Command              | Description                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `agents-cli install` | Install dependencies using uv                                                         |
| `agents-cli playground` | Launch local development environment                                                  |
| `agents-cli lint`    | Run code quality checks                                                               |
| `agents-cli eval`    | Evaluate agent behavior (generate, grade, analyze, and more — see `agents-cli eval --help`) |
| `make test` | Run unit and integration tests (see [Testing](#testing) below) |
| `make lint` | Run ruff + pylint (see [Linting](#linting) below) |
| `agents-cli deploy`  | Deploy agent to Agent Runtime                                                                |
| `agents-cli publish gemini-enterprise` | Register deployed agent to Gemini Enterprise                    |

## 🛠️ Project Management

| Command | What It Does |
|---------|--------------|
| `agents-cli scaffold enhance` | Add CI/CD pipelines and Terraform infrastructure |
| `agents-cli infra cicd` | One-command setup of entire CI/CD pipeline + infrastructure |
| `agents-cli scaffold upgrade` | Auto-upgrade to latest version while preserving customizations |

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
uv run pytest tests/unit tests/integration
```

### Run a single test file

```bash
uv run pytest tests/integration/test_agent.py
```

### Run one specific test

```bash
uv run pytest tests/integration/test_agent.py::test_auto_approve_low_amount
```

### What to expect

- **4 tests** should pass (1 unit + 3 integration).
- A few deprecation warnings from Google ADK libraries are normal and can be ignored for now.

---

## Linting

This project uses two complementary tools:

| Tool | Role | Auto-fix? |
| ---- | ---- | --------- |
| **ruff** | Fast style checks (imports, formatting, common bugs) | Yes |
| **pylint** | Deeper code-quality checks (design, naming, logic smells) | No — reports issues for you to fix manually |

### Run all linters

```bash
make lint
```

Equivalent commands:

```bash
uv run ruff check expense_agent tests
uv run pylint expense_agent tests
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

Edit your agent logic in `expense_agent/agent.py` and test with `agents-cli playground` — it auto-reloads on save.

## Deployment

```bash
gcloud config set project <your-project-id>
agents-cli deploy
```

To add CI/CD and Terraform, run `agents-cli scaffold enhance`.
To set up your production infrastructure, run `agents-cli infra cicd`.

## Observability

Built-in telemetry exports to Cloud Trace, BigQuery, and Cloud Logging.

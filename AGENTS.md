# AGENTS.md

Minimal guide for AI coding agents working in this repo. For full context see
[`GEMINI.md`](GEMINI.md) (development phases) and [`README.md`](README.md)
(setup, commands, testing, linting).

## Project

Ambient expense-approval agent built on Google ADK 2.0 (`Workflow`). Agent
code lives in `expense_agent/` (not `app/`). Python 3.12, managed with `uv`.

## Key Commands

| Command | Purpose |
|---------|---------|
| `make install` | Install deps + dev tools + git hooks |
| `make test` | Run unit + integration tests (`uv run pytest tests/unit tests/integration`) |
| `make lint` | ruff + pylint |
| `make lint-fix` | ruff auto-fix + format |
| `agents-cli playground` | Interactive local testing |

## Architecture (workflow nodes)

`parse_event` → `route_expense` → (`auto_approver` | `security_checkpoint`)
→ (`llm_reviewer` | `human_approval`)

- `route_expense`: amount < `THRESHOLD_USD` (100.0) → `auto_approve`; else → `security_check`.
- `security_checkpoint`: scrubs PII (SSN → `[REDACTED_SSN]`, CC → `[REDACTED_CC]`),
  then checks for prompt-injection keywords → `security_event` (HIGH risk) or `clean`.
- `llm_reviewer`: LlmAgent (`REVIEW_MODEL`) producing a `RiskAssessment`.
- `human_approval`: pauses for human sign-off on flagged expenses.

## Critical Invariants

- **Never log or emit raw PII.** SSNs and credit-card numbers must be redacted
  by `security_checkpoint` before reaching any downstream node.
- **Prompt-injection phrases** route directly to `human_approval` as a security
  event — they must never reach `llm_reviewer`.
- **Do not change `REVIEW_MODEL`** unless explicitly asked.
- **Node functions are wrapped by `@node`** into `FunctionNode` objects (not
  directly callable). Access the underlying callable via `node._func` in tests.

## Testing Notes

- Unit tests call node logic via `node._func(...)` and inspect
  `event.actions.route` and `event.output`.
- Integration tests run the full `Workflow` via `Runner` + `InMemorySessionService`.
- The injection and PII paths do not call the LLM; the clean-expense path does.

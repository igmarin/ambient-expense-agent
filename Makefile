.PHONY: install install-hooks test lint lint-fix

install: install-hooks
	uv sync --group dev

install-hooks:
	chmod +x .githooks/pre-commit
	git config core.hooksPath .githooks

test:
	uv run pytest tests/unit tests/integration

lint:
	uv run ruff check expense_agent tests
	uv run pylint expense_agent tests

lint-fix:
	uv run ruff check --fix expense_agent tests
	uv run ruff format expense_agent tests
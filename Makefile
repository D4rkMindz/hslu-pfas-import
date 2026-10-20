.PHONY: test

up:
	@docker compose up -d

down:
	@docker compose down

lint:
	@uv run ruff check --watch --fix

lint-ci:
	@uv run ruff check

test:
	@uv run pytest

ci: lint-ci test

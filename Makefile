up:
	@docker compose up -d

down:
	@docker compose down

stop:
	@docker compose down -v --rmi all --remove-orphans

lint:
	@uv run ruff check --watch --fix

lint-ci:
	@uv run ruff check

tests:
	@uv run --env-file .env pytest

ci: lint-ci tests
up:
	@docker compose up -d

down:
	@docker compose down

lint:
	@uv run ruff check --watch --fix

test:
	@uv run pytest
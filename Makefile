.PHONY: test
COMPOSE := docker compose --env-file .env --env-file .env.docker
up:
	@$(COMPOSE) up -d

build:
	@$(COMPOSE) build

down:
	@$(COMPOSE) down

stop:
	@$(COMPOSE) down -v --rmi all --remove-orphans

lint:
	@uv run ruff check --watch --fix

lint-ci:
	@uv run ruff check

test:
	@uv run pytest

ci: lint-ci test

.DEFAULT_GOAL := help
.PHONY: help install db db-stop seed serve worker web test lint format web-lint web-build eval-offline eval-smoke eval-real summary docker-build docker-up clean

export SCOUT_DATABASE_URL ?= postgresql://scout:scout@127.0.0.1:55470/scout
export TEST_DATABASE_URL ?= postgresql://scout:scout@127.0.0.1:55470/scout_test
FREE_MODEL ?= nvidia/nemotron-3-super-120b-a12b:free
FREE_FALLBACKS ?= nvidia/nemotron-3-ultra-550b-a55b:free
REAL = SCOUT_LLM_PROVIDER=openrouter SCOUT_LLM_MODEL=$(FREE_MODEL) SCOUT_LLM_FALLBACK_MODELS=$(FREE_FALLBACKS)

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Python dependencies (uv) and the dashboard's (npm)
	uv sync
	cd web && npm ci --no-audit --no-fund

db:  ## Local PostgreSQL 17 on port 55470 (with a test database)
	docker run -d --name scout-db -e POSTGRES_USER=scout -e POSTGRES_PASSWORD=scout -e POSTGRES_DB=scout \
		-p 127.0.0.1:55470:5432 postgres:17-alpine
	@until docker exec scout-db pg_isready -U scout >/dev/null 2>&1; do sleep 1; done
	docker exec scout-db psql -U scout -c "CREATE DATABASE scout_test" || true

db-stop:  ## Remove the local database container
	docker rm -f scout-db

seed:  ## Generate the synthetic web and load the 60 demo accounts
	uv run scout seed

serve:  ## API on http://localhost:8000
	uv run scout serve --port 8000

worker:  ## Background worker (jobs, due sends, reply classification, retention)
	uv run scout worker --inbox data/inbox

web:  ## Next.js dev server on http://localhost:3000
	cd web && npm run dev

test:  ## Python tests (no API keys; database tests need TEST_DATABASE_URL)
	uv run pytest

lint:  ## Ruff lint + format check + mypy (strict)
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy

format:  ## Auto-format and fix lint issues
	uv run ruff format src tests
	uv run ruff check --fix src tests

web-lint:  ## ESLint + TypeScript type check
	cd web && npm run lint && npm run typecheck

web-build:  ## Production build of the dashboard
	cd web && npm run build

eval-offline:  ## Every suite with the offline model (no API calls)
	uv run scout seed --reset
	uv run scout eval all --name offline
	uv run scout eval injection --name offline
	uv run scout eval llm_only --name offline --subset
	uv run scout eval compliance --name offline

eval-smoke:  ## List free OpenRouter models and smoke-test the chosen ones (needs OPENROUTER_API_KEY)
	SCOUT_LLM_PROVIDER=openrouter uv run scout eval free-models
	SCOUT_LLM_PROVIDER=openrouter uv run scout eval smoke --model $(FREE_MODEL) --fallbacks $(FREE_FALLBACKS)

eval-real:  ## The real run behind the README (needs OPENROUTER_API_KEY; ~450 requests, every model :free)
	uv run scout seed --reset
	$(REAL) uv run scout eval accounts --name main
	$(REAL) uv run scout eval drafts --name main
	$(REAL) uv run scout eval replies --name main --no-rules
	$(REAL) uv run scout eval replies --name main
	$(REAL) uv run scout eval claims --name main
	$(REAL) uv run scout eval injection --name main
	$(REAL) uv run scout eval llm_only --name main --subset
	uv run scout eval compliance --name main
	uv run scout eval summary

summary:  ## Fold results/*.json and the call ledger into results/summary.json
	uv run scout eval summary

docker-build:  ## Build the API and dashboard images
	docker compose build

docker-up:  ## Everything in Docker: dashboard :3000, API :8000, Mailpit :8025, synthetic web :8090
	docker compose up --build

clean:  ## Remove caches (keeps results/)
	rm -rf .pytest_cache .mypy_cache .ruff_cache web/.next

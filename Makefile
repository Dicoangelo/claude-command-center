.PHONY: help test test-cov lint format typecheck generate serve backup fix-data all

help: ## Show all available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

test: ## Run test suite
	python3 -m pytest tests/ -v --tb=short

test-cov: ## Run tests with coverage report
	python3 -m pytest tests/ -v --tb=short --cov=scripts --cov=config --cov-report=term-missing

lint: ## Run ruff linter on all Python files
	ruff check scripts/ config/

format: ## Auto-format Python files with ruff
	ruff format scripts/ config/

typecheck: ## Run mypy type checker
	mypy scripts/ config/ --ignore-missing-imports

generate: ## Generate static dashboard HTML
	bash scripts/ccc-generator.sh

serve: ## Start the live API server on port 8766
	python3 scripts/ccc-api-server.py

backup: ## Backup the SQLite database with timestamp
	python3 scripts/ccc-backup.py

fix-data: ## Scan transcripts and reconcile dashboard data
	python3 scripts/fix-all-dashboard-data.py

all: lint format typecheck test ## Run lint, format, typecheck, and tests

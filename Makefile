include custom.mk

unexport PYTHONPATH

setup-env:
	@[ ! -f ./.env ] && cp ./.env.example ./.env || echo ".env file already exists."

start: ## Start the docker containers
	@echo "Starting the docker containers"
	@docker compose up

stop: ## Stop Containers
	@docker compose down

restart: stop start ## Restart Containers

start-bg:  ## Run containers in the background
	@docker compose up -d

dev: ## Start Django and npm dev servers
	@./scripts/dev.sh

django: ## Run Django dev server
	@uv run manage.py runserver

manage: ## Run any manage.py command. E.g. `make manage ARGS='createsuperuser'`
	@uv run manage.py ${ARGS}

migrations: ## Create DB migrations in the container
	@uv run manage.py makemigrations

migrate: ## Run DB migrations in the container
	@echo "Waiting for database to be ready..."
	@until docker compose exec db pg_isready -d spicy -U postgres >/dev/null 2>&1; do echo "Database not ready, waiting..."; sleep 2; done
	@echo "Database is ready, running migrations..."
	@uv run manage.py migrate

shell: ## Get a Django shell
	@uv run manage.py shell

dbshell: ## Get a Database shell
	@docker compose exec db psql -U postgres spicy

backup: ## Snapshot the database + media into backups/
	@./scripts/backup_db.sh

restore: ## Restore a snapshot: make restore ARGS='backups/spicy-<ts>.sql.gz [--yes]'
	@./scripts/restore_db.sh $(ARGS)

drop-test-db:  ## Drop the test database (use when stuck from --keepdb)
	@docker compose exec db psql -U postgres -c "DROP DATABASE IF EXISTS test_spicy WITH (FORCE)"

test: ## Run Django tests
	@uv run manage.py test ${ARGS}

init: setup-env start-bg migrations migrate npm-install-all  ## Quickly get up and running (start containers and bootstrap DB)
	@echo ""
	@echo "✅ Project setup complete! Run 'make dev' to start the app."

uv: ## Run a uv command
	@uv $(filter-out $@,$(MAKECMDGOALS))

uv-sync: ## Sync dependencies
	@uv sync --frozen

ruff-format: ## Runs ruff formatter on the codebase
	@uv run ruff format .

ruff-lint:  ## Runs ruff linter on the codebase
	@uv run ruff check --fix .

ruff: ruff-format ruff-lint ## Formatting and linting using Ruff

djlint-format: ## Runs djlint formatter on HTML templates
	@uv run djlint --reformat templates

djlint-check: ## Check HTML templates with djlint
	@uv run djlint templates

type-check: ## Run Python type checking
	@uv run mypy .

npm-install-all: ## Runs npm install
	@npm install

npm-install: ## Runs npm install (optionally accepting package names)
	@npm install $(filter-out $@,$(MAKECMDGOALS))

npm-uninstall: ## Runs npm uninstall (takes package name(s))
	@npm uninstall $(filter-out $@,$(MAKECMDGOALS))

npm-build: ## Runs npm build (for production assets)
	@npm run build

npm-dev: ## Runs npm dev
	@npm run dev

npm-type-check: ## Runs the type checker on the front end TypeScript code
	@npm run type-check

upgrade: migrations migrate npm-install npm-dev  ## Run after a Pegasus upgrade to update requirements, migrate the database, and rebuild the front end

.PHONY: help
.DEFAULT_GOAL := help

help:
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

# catch-all for any undefined targets - this prevents error messages
# when running things like make npm-install <package>
%:
	@:

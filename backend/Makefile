.PHONY: local local-container build deploy deploy-guided smoke logs test lint clean

STACK := recsbot-backend
REGION := eu-west-1

local:
	PYTHONPATH=src APP_MODE=local python -m uvicorn app:app \
		--host 127.0.0.1 --port 8000

local-container:
	mkdir -p runtime
	docker compose up --build

build:
	sam build -t infra/template.yaml

deploy: build
	sam deploy --no-confirm-changeset

deploy-guided: build
	sam deploy --guided

smoke:
	@./scripts/smoke.sh

logs:
	sam logs --stack-name $(STACK) --name ChatStreamFunction --tail --region $(REGION)

test:
	pytest

lint:
	ruff check src tests
	ruff format --check src tests

clean:
	rm -rf .aws-sam build dist .pytest_cache .mypy_cache .ruff_cache

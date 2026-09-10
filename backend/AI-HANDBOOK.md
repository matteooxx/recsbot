# AI Handbook: recsbot backend

Read `../AI-HANDBOOK.md` first, then this file before changing the backend or
its API contract.

## Status And Scope

This personal project is a technically public-ready snapshot. It provides
conversation, preference, feedback, recommendation, and SSE chat APIs for
`../ui`.

Local mode is the supported default. The AWS SAM stack is optional reference
functionality and must not be required for tests or normal development.

## Runtime Adapters

- `src/storage.py`: selects SQLite or DynamoDB persistence
- `src/chat_client.py`: selects deterministic, Ollama, or Bedrock chat
- `src/auth.py`: selects environment-token or managed-secret authentication
- `src/local_store.py`: local conversation/preference implementation
- `src/local_model.py`: offline recommender and optional Ollama adapter
- `src/dynamo.py`, `src/bedrock_client.py`: optional cloud adapters
- `src/app.py`: stable FastAPI/SSE contract

Keep the frontend contract compatible when changing event names, message
blocks, auth headers, conversation IDs, cursor behavior, feedback, or
preferences.

## No-Cloud Runtime

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-local.txt
mkdir -p runtime
PYTHONPATH=src python -m uvicorn app:app \
  --host 127.0.0.1 --port 8000
```

Defaults are SQLite plus deterministic recommendations. Set
`MODEL_BACKEND=ollama`, `OLLAMA_BASE_URL`, and `OLLAMA_MODEL` to use a local
model. The deterministic fallback remains available if Ollama is unreachable.

Docker/NAS:

```bash
mkdir -p runtime
docker compose up --build -d
```

The container binds to loopback by default. Before binding to a LAN address,
set a strong `RECSBOT_AUTH_TOKEN` and protect the port with a firewall or
authenticated reverse proxy. Persist and back up `runtime/recsbot.db` as
private data.

### Amazon Replacement Map

| Optional AWS component | PC/NAS replacement |
| --- | --- |
| Lambda Function URL | FastAPI under Uvicorn or the Docker service |
| DynamoDB | SQLite via `src/local_store.py` |
| Bedrock | deterministic model or local Ollama via `src/local_model.py` |
| Secrets Manager | process environment or a mode-`0600` local env file |
| CloudWatch | container/process logs retained by the host |

## Checks

```bash
python -m pytest
ruff check src tests
ruff format --check src tests
docker compose config
curl -fsS http://127.0.0.1:8000/health
```

Optional cloud checks are `sam validate -t infra/template.yaml` and
`sam build -t infra/template.yaml`. A cloud deployment requires an explicit
account/profile review, model availability check, a trusted arm64 Lambda Web
Adapter layer ARN, parameter review, and change set inspection.

## Data And Publication Rules

Never commit tokens, API keys, cloud credentials, conversation exports,
database files, model logs, deployment state, private library data, or live
endpoints. Keep local and cloud adapters behaviorally aligned without making
cloud imports mandatory in local mode.

Publish only sanitized `main` after tests, tree/history gitleaks scans, and a
frontend compatibility check. Update this file after adapter, API, persistence,
auth, model, or deployment changes.

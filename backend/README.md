# recsbot-backend

Recommendation-chat API with conversations, preferences, feedback, local
library context, and server-sent events.

Local mode is the default and requires no Amazon backend:

- SQLite stores conversations and preferences.
- A deterministic recommender works offline.
- Ollama can provide a local language model.
- The optional SAM stack retains the original cloud architecture.

## Local quick start

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-local.txt
mkdir -p runtime
PYTHONPATH=src python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Check <http://127.0.0.1:8000/health>.

To use Ollama:

```bash
MODEL_BACKEND=ollama \
OLLAMA_BASE_URL=http://127.0.0.1:11434 \
OLLAMA_MODEL=llama3.2:3b \
PYTHONPATH=src python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Ollama failures fall back to deterministic recommendations.

## Docker or NAS

```bash
mkdir -p runtime
docker compose up --build -d
```

The default bind is loopback. For LAN access, set a strong
`RECSBOT_AUTH_TOKEN`, set `RECSBOT_BIND_ADDRESS=0.0.0.0`, and protect the port
with a firewall or authenticated reverse proxy.

## API

- `GET /health`
- `POST /chat` (SSE)
- conversation list/read/update/delete
- preferences read/update
- assistant feedback

Clients send `X-Recsbot-Token` when a token is configured.

## Checks

```bash
python -m pytest
ruff check src tests
ruff format --check src tests
docker compose config
```

The optional cloud path uses `infra/template.yaml`; provide a trusted
`LambdaWebAdapterLayerArn`, then review every parameter, IAM permission, model
availability, and the complete change set before deployment.

## License

This project is available under the [MIT License](LICENSE).

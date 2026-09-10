# recsbot

Self-hosted recommendation chat. The backend (FastAPI) stores conversations,
preferences, and feedback, and streams recommendations over server-sent
events. The web UI (Flask) handles login, proxies the backend's JSON and SSE
APIs, and can add read-only Steam and Jellyfin taste data.

No cloud account is required. By default the backend uses SQLite and a
deterministic recommender; a local Ollama model is optional, and Ollama
failures fall back to the deterministic recommender. The original AWS SAM
stack is kept under `backend/infra/` as an optional architecture reference.

## Architecture

```text
browser --> ui/ (Flask, 127.0.0.1:5001) --> backend/ (FastAPI + SSE, 127.0.0.1:8000)
            login, sessions, API proxy       SQLite in runtime/recsbot.db
            optional Steam / Jellyfin        deterministic model or Ollama
```

## Repository Layout

```text
recsbot/
├── backend/          FastAPI API, SQLite store, recommenders, optional SAM stack
├── ui/               Flask frontend, templates, static assets, integrations
├── .github/          CI for both components
├── AI-HANDBOOK.md    rules for automated contributors
├── SECURITY.md       reporting and deployment boundary
└── LICENSE
```

## Quick Start

Start the backend:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-local.txt
mkdir -p runtime
PYTHONPATH=src python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Check <http://127.0.0.1:8000/health>. In a second terminal, start the UI:

```bash
cd ui
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p runtime
python scripts/init-local-env.py
make run
```

Open <http://127.0.0.1:5001>.

## Shared Backend Token

A loopback-only setup can leave the token empty. For anything else, generate
one long random value and give it to both components:

| Component | Variable | Set it in |
| --- | --- | --- |
| backend | `RECSBOT_AUTH_TOKEN` | the process environment (see `backend/local.env.example`) or the Compose environment |
| ui | `RECSBOT_BEARER_TOKEN` | `ui/runtime/app.env` |

The UI sends the value to the backend in the `X-Recsbot-Token` header.

## Docker or NAS

Each component has its own `Dockerfile` and `compose.yaml`, and both bind to
loopback by default:

```bash
(cd backend && mkdir -p runtime && docker compose up --build -d)
(cd ui && mkdir -p runtime && python scripts/init-local-env.py \
  && docker compose up --build -d)
```

Inside Docker the UI reaches the backend at `http://host.docker.internal:8000`.
Set `RECSBOT_BIND_ADDRESS` (backend) or `RECSBOT_UI_BIND_ADDRESS` (UI) to a
non-loopback address only behind a firewall or an authenticated reverse proxy,
and only with the shared token set. Keep `ui/runtime/app.env` at mode `0600`
and back up `backend/runtime/recsbot.db` as private data.

## Checks

```bash
(cd backend && make lint && make test && docker compose config --quiet)
(cd ui && make check)
```

CI runs these checks, `pip-audit`, and both image builds on every push and
pull request.

## Documentation

- [`backend/README.md`](backend/README.md): API, Ollama, optional cloud path
- [`ui/README.md`](ui/README.md): configuration, Steam and Jellyfin
- [`SECURITY.md`](SECURITY.md): reporting and deployment boundary
- [`AI-HANDBOOK.md`](AI-HANDBOOK.md): rules for automated contributors

## License

Application source is available under the [MIT License](LICENSE). Bundled font
licenses are listed in [`ui/THIRD_PARTY_NOTICES.md`](ui/THIRD_PARTY_NOTICES.md).

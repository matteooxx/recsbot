# AI Handbook: recsbot

Read this file, then the handbook of the component you are changing:

- `backend/AI-HANDBOOK.md`: FastAPI/SSE API, storage, model and auth adapters,
  optional AWS SAM stack
- `ui/AI-HANDBOOK.md`: Flask frontend, SSE proxy, optional Steam and Jellyfin
  adapters

## Repository Rules

- The backend and the UI are versioned together. A change to SSE framing,
  conversation payloads, feedback, preferences, or auth headers must update
  and verify both `backend/` and `ui/` in the same change.
- Local mode is the supported default. The SAM stack under `backend/infra/` is
  optional reference functionality and must never be required by tests or CI.
- Repository tooling lives at the root: `.gitattributes` (LF line endings),
  `.gitignore`, `.gitleaks.toml`, and `.github/workflows/ci.yml`. Each CI job
  runs one component's documented checks inside that component's directory.
- Each component keeps its own `Dockerfile` and `compose.yaml`; build and run
  them from their own directory.

## Checks

```bash
(cd backend && make lint && make test && docker compose config --quiet)
(cd ui && make check && mkdir -p runtime && touch runtime/app.env \
  && docker compose config --quiet)
```

The UI's `compose.yaml` reads `runtime/app.env`; an empty file is enough for
the configuration check. Never commit a populated one.

## Data And Publication Rules

Never commit `runtime/`, databases, populated environment files (`.env`,
`local.env`, `app.env`), tokens, API keys, password hashes, Flask secrets,
SMTP credentials, Steam or Jellyfin keys, conversation exports, private media
history, deployment state, or live endpoints.

Publish only the reviewed `main` branch after the checks above and gitleaks
scans of both the tree and the history. Update this file when the repository
layout, CI, or the backend/UI contract changes.

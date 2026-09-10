# AI Handbook: recsbot web UI

Read `../AI-HANDBOOK.md` first, then this file before changing the frontend.

## Status And Scope

This personal project is a technically public-ready snapshot. It is an
authenticated Flask frontend and SSE proxy for `../backend`, with optional
read-only Steam and Jellyfin taste adapters.

The frontend has no cloud requirement and must continue to work with the local
backend.

## No-Cloud Runtime

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p runtime
python scripts/init-local-env.py
make run
```

Start the backend at `127.0.0.1:8000`, then open
<http://127.0.0.1:5001>. Docker/NAS instructions are in `README.md`.

Steam and Jellyfin are optional. Missing credentials must yield an empty taste
profile, not a startup failure. HTTPS certificate verification defaults to on.
Do not reintroduce a global `verify=False`; install a private CA where needed.

### Amazon Replacement Map

| Former hosted component | PC/NAS replacement |
| --- | --- |
| Lambda/Bedrock chat endpoint | local backend (`../backend`) at `127.0.0.1:8000` |
| EC2 signing proxy | direct HTTP/HTTPS connection to the local backend |
| Secrets Manager | generated values in ignored `runtime/app.env` |
| Hosted media integrations | optional direct read-only Steam/Jellyfin adapters |

## Architecture

- `app.py`: pages, backend proxy, settings and health APIs
- `auth.py`: bcrypt login/session and reset flow
- `settings.py`: process overlay and mode-`0600` env persistence
- `taste_profile.py`: cached aggregate profile
- `steam.py`, `jellyfin.py`: optional read-only integrations
- `static/`, `templates/`: browser UI
- `scripts/init-local-env.py`: secret/password bootstrap

Changes to SSE framing, conversation payloads, feedback, preferences, or auth
headers require paired backend verification.

## Checks

```bash
make check
docker compose config
```

Manually test login/logout, password changes, backend health, settings,
Steam/Jellyfin failure behavior, chat streaming, regeneration, conversation
CRUD/trash, feedback, mobile layout, keyboard access, and browser errors.

## Data And Publication Rules

Never commit `runtime/app.env`, password hashes, Flask secrets, SMTP
credentials, backend tokens, Steam/Jellyfin keys, private media history,
conversation exports, or host-specific deployment state.

Publish only sanitized `main` after tests and tree/history secret scans. Update
this handbook after backend-contract, authentication, settings, integration,
or deployment changes.

The bundled Fraunces and Inter Tight fonts retain the SIL Open Font License;
see `THIRD_PARTY_NOTICES.md` and `static/fonts/OFL-1.1.txt`.

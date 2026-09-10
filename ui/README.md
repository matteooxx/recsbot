# recsbot-ui

Authenticated Flask frontend for the recsbot recommendation service. It proxies
the backend's JSON and SSE APIs, maintains the browser session, and optionally
adds read-only Steam and Jellyfin taste data.

The frontend has no cloud dependency. By default it expects a local
`recsbot-backend` at `http://127.0.0.1:8000`; in Docker it uses
`http://host.docker.internal:8000`.

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p runtime
python scripts/init-local-env.py
make run
```

Open <http://127.0.0.1:5001>. Start the backend separately and use the same
`RECSBOT_BEARER_TOKEN` in both projects when a token is configured.

Steam and Jellyfin are optional. Leaving their IDs and keys empty produces an
empty taste profile without breaking chat.

## Docker or NAS

```bash
mkdir -p runtime
python scripts/init-local-env.py
docker compose up --build -d
```

The Compose service binds to loopback by default. On a NAS, set
`RECSBOT_UI_BIND_ADDRESS=0.0.0.0` only when access is protected by the host
firewall or a trusted reverse proxy. Keep `runtime/app.env` at mode `0600` and
back up that file separately from source.

If the backend runs in another container or machine, set `RECSBOT_BASE_URL` in
`runtime/app.env` to its reachable URL. Keep `RECSBOT_TLS_VERIFY=true` for
HTTPS. A private certificate should be trusted by the container rather than
disabling verification.

## Configuration

See `app.env.example`. Required values are:

- `ADMIN_PASS_HASH`
- `SECRET_KEY`
- `RECSBOT_BASE_URL`

Optional values configure email-based password reset, the shared backend token,
Steam, and Jellyfin. Secrets and local runtime files are ignored by Git.

## Checks

```bash
make check
docker compose config
```

There is not yet a complete automated browser suite. Manually verify login,
logout, settings, backend health, chat streaming, conversation CRUD, feedback,
and narrow/mobile layouts after frontend changes.

## License

Application source is available under the [MIT License](LICENSE). Bundled font
licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

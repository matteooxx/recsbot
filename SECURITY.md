# Security Policy

## Supported Version

Security fixes are applied to the current `main` branch only. The optional AWS
SAM stack under `backend/infra/` is retained as an architecture reference and
is not supported as a deployment.

## Reporting

Do not open a public issue for a suspected vulnerability. Use the repository's
private vulnerability reporting ("Report a vulnerability" under the Security
tab). Include the affected commit and component, reproduction steps, impact,
and any suggested mitigation.

## Deployment Boundary

recsbot is a single-user, self-hosted application. The backend binds to
`127.0.0.1:8000` and the UI to `127.0.0.1:5001` by default; neither is designed
to be exposed directly to the public internet. When `RECSBOT_AUTH_TOKEN` is
empty, the backend does not require a token.

Before binding either component to a non-loopback address:

- set the same long, random value as `RECSBOT_AUTH_TOKEN` in the backend and
  `RECSBOT_BEARER_TOKEN` in the UI;
- place the services behind a firewall or an authenticated HTTPS reverse
  proxy;
- keep `ui/runtime/app.env` at mode `0600`;
- keep TLS verification on for an HTTPS backend and trust a private CA in the
  container instead of disabling verification;
- back up `backend/runtime/recsbot.db` and treat it as private data.

## Existing Controls

- UI login with bcrypt password hashes and server-side Flask sessions;
- backend requests authenticated with the `X-Recsbot-Token` header when a
  token is configured;
- TLS certificate verification for UI-to-backend calls, on by default;
- Compose services with a read-only root filesystem, all Linux capabilities
  dropped, `no-new-privileges`, and a `noexec` temporary filesystem; the
  backend image also runs as a non-root user;
- loopback-only default binds for both services;
- CI that runs lint, tests, `pip-audit`, and both image builds on every push
  and pull request.

## Secret and Data Handling

Never commit or attach:

- `ui/runtime/app.env`, `backend/local.env`, `.env`, or any other populated
  environment file; only the `*.example` templates are tracked;
- `runtime/` directories, SQLite databases, or backups;
- password hashes, Flask secrets, SMTP credentials, backend tokens, or Steam,
  Jellyfin, and cloud credentials;
- conversation exports, private media history, deployment state, or live
  endpoints.

## Third-Party Services

Steam and Jellyfin integrations are optional and read-only; with their
credentials unset, the UI builds an empty taste profile. When the backend is
configured to use Ollama, conversation context is sent to that endpoint. Point
optional integrations only at services you control or trust.

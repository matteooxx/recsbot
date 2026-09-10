"""Application bearer-token check for local and optional cloud deployments."""

import hmac

from fastapi import Header, HTTPException, status

import config


def _expected_token() -> str:
    if config.AUTH_BACKEND == "environment":
        return config.RECSBOT_AUTH_TOKEN
    if config.AUTH_BACKEND != "secrets-manager":
        raise RuntimeError("AUTH_BACKEND must be 'environment' or 'secrets-manager'")
    import aws_secrets

    payload = aws_secrets.get(config.BEARER_TOKEN_SECRET)
    token = payload.get("token") or payload.get("value")
    if not token:
        raise RuntimeError(f"secret {config.BEARER_TOKEN_SECRET} missing 'token' or 'value' field")
    return token


async def require_bearer(
    x_recsbot_token: str | None = Header(default=None, alias="X-Recsbot-Token"),
) -> None:
    expected = _expected_token()
    # Empty token is permitted for loopback-only development. Bind to a LAN
    # address only after setting a strong token.
    if not expected:
        return
    if not x_recsbot_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing X-Recsbot-Token"
        )
    presented = x_recsbot_token.strip()
    if not hmac.compare_digest(presented, expected):
        if config.AUTH_BACKEND == "secrets-manager":
            import aws_secrets

            aws_secrets.invalidate(config.BEARER_TOKEN_SECRET)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")

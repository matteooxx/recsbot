"""Select the chat adapter without importing boto3 in local mode."""

from __future__ import annotations

import config

if config.MODEL_BACKEND in {"deterministic", "ollama"}:
    from local_model import *  # noqa: F403
elif config.MODEL_BACKEND == "bedrock":
    from bedrock_client import *  # noqa: F403
else:
    raise RuntimeError(
        "MODEL_BACKEND must be 'deterministic', 'ollama', or 'bedrock' "
        f"(got {config.MODEL_BACKEND!r})"
    )

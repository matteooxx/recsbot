"""Select the persistence adapter without importing cloud SDKs in local mode."""

from __future__ import annotations

import config

if config.STORAGE_BACKEND == "sqlite":
    from local_store import *  # noqa: F403
elif config.STORAGE_BACKEND == "dynamodb":
    from dynamo import *  # noqa: F403
else:
    raise RuntimeError(
        f"STORAGE_BACKEND must be 'sqlite' or 'dynamodb' (got {config.STORAGE_BACKEND!r})"
    )

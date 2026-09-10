#!/bin/sh
exec python -m uvicorn app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --no-access-log

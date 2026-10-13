#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pip install -r backend/requirements-dev.lock
(cd backend && ../.venv/bin/pytest -q && ../.venv/bin/ruff check app tests)
(cd frontend && npm ci && npm run build)

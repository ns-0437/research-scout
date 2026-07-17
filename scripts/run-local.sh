#!/usr/bin/env bash
# Run the agent locally on :9000.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] && export $(grep -v '^#' .env | xargs) || true
echo "Agent on http://localhost:9000  (POST /run, /test, GET /health)"
uvicorn app:app --host 0.0.0.0 --port 9000 --reload

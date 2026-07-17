#!/usr/bin/env bash
# Fire a sample /test request at your locally-running agent (no signature needed
# when SITREP_AGENT_SECRET is unset). Run scripts/run-local.sh first.
set -euo pipefail
curl -s --max-time 600 -X POST http://localhost:9000/test \
  -H 'Content-Type: application/json' \
  -d '{
    "task": {"id": "t1", "title": "Research the competitors and tools discussed in the pricing meeting", "description": "We need to know what we are up against before the pricing page ships."},
    "summary": "The team met to finalize Q3 pricing for our meeting-notes SaaS. Priya said Otter.ai recently changed their pricing and we should check what their business tier costs now. Nobody was sure whether Fireflies.ai offers a free tier anymore. Marcus claimed that most AI meeting assistants are moving to per-seat pricing rather than usage-based; we agreed to verify that before committing. We also discussed whether to use Stripe Billing or Paddle for the new plans; the merchant-of-record question around EU VAT was left open. Launch target is end of Q3.",
    "attendees": [{"id": "a1", "name": "Priya"}, {"id": "a2", "name": "Marcus"}, {"id": "a3", "name": "Navin"}],
    "agent": {"instructions": "", "tools": [], "model": "claude-opus-4-8"}
  }' | python -m json.tool

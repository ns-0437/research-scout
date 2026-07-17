#!/usr/bin/env bash
# Expose your local agent to the internet so SitRep can reach it during a live
# demo. Prints a public https URL — paste it (without a trailing slash) into the
# Studio's "Endpoint URL" field.
#
# Requires cloudflared:  brew install cloudflared   (or see Cloudflare docs)
# Tunnels are fine for demos but die when your laptop sleeps — for the FINAL
# submission, deploy instead (see README "Deploy").
set -euo pipefail
exec cloudflared tunnel --url http://localhost:9000

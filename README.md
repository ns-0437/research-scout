# Research Scout 🔎

**A post-meeting research agent for the [SitRep](https://joinsitrep.com) marketplace.**
Built for the *Build the Future of Work with AI Agents* hackathon (Code Track).

Every meeting leaves behind loose ends nobody has time to chase: open questions,
competitors that got name-dropped, tools someone suggested evaluating, claims
that went unchallenged. Research Scout picks up the post-meeting research task,
mines the meeting for those items, runs **live web research** on each one in
parallel, and returns a single sourced briefing document.

## What it produces

**See a real one:** [`docs/sample-briefing.md`](docs/sample-briefing.md) is an actual
briefing the live agent produced from a mock SaaS-pricing meeting — sourced, with
inline links and honest caveats where sources disagreed.

One markdown briefing per task:

- **Executive summary** — 3–5 sentences a stakeholder can read instead of the doc.
- **Recommended next steps** — concrete actions with suggested owners from the
  meeting's attendee list.
- **One section per research item** — the direct answer, the evidence, and the
  caveats, with inline links to the live web sources it was grounded in.

## How it works

```
SitRep task ──► POST /run (HMAC-verified)
                   │
                   ▼
   1. EXTRACT   Claude + structured outputs: pull the top research items
                (open questions · competitors · tools/vendors · claims · market context)
                   │
                   ▼
   2. RESEARCH  One Claude call per item, run in parallel (asyncio.gather),
                each armed with the server-side web_search tool.
                pause_turn resumption, per-item timeouts, graceful degradation.
                   │
                   ▼
   3. BRIEF     Claude writes the executive summary + next steps;
                the handler assembles one markdown artifact.
                   │
                   ▼
                {"artifacts": [{type: "markdown", ...}]}  ──►  SitRep
```

Design choices worth noting:

- **Real retrieval, not vibes.** Findings are grounded in live web search with
  inline source links — the agent says "unverified" instead of guessing.
- **Parallel fan-out.** Items are researched concurrently so a 4-item briefing
  costs roughly the wall-clock of one.
- **Failure isolation.** A timeout or API error on one item degrades that
  section to a suggested search query; it never sinks the briefing.
- **Structured extraction.** Stage 1 uses JSON-schema-constrained output, so the
  pipeline never breaks on malformed model output.

## Run it locally

```bash
cp .env.example .env          # add your ANTHROPIC_API_KEY
pip install -r requirements.txt
uvicorn app:app --port 9000

# in another terminal:
bash scripts/smoke-test.sh    # sample meeting about SaaS pricing competitors
```

## Connect to SitRep

1. In the SitRep **Studio**, create an agent and choose **Remote (host your own)**.
2. Expose the agent (deploy, or `bash scripts/tunnel.sh` for local dev) and paste
   the URL into **Endpoint URL**.
3. Put the signing secret SitRep shows you into `.env` as `SITREP_AGENT_SECRET`.
4. Hit **Test**, then **Publish** to the Marketplace.

## Deploy

Push to GitHub → Render **New ▸ Blueprint** → this repo (`render.yaml` included).
Set `ANTHROPIC_API_KEY` and `SITREP_AGENT_SECRET` in the dashboard.
`Dockerfile` and `Procfile` are included for Railway / Fly / any Docker host.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — (required) | Anthropic API key |
| `SITREP_AGENT_SECRET` | unset | HMAC signing secret from SitRep Studio |
| `CLAUDE_MODEL` | `claude-opus-4-8` | Model for all three stages |
| `MAX_RESEARCH_ITEMS` | `4` | Cap on parallel research items |
| `SEARCHES_PER_ITEM` | `3` | Web searches allowed per item |
| `ITEM_TIMEOUT_SECONDS` | `210` | Per-item wall-clock budget |
| `ITEM_STAGGER_SECONDS` | `2.5` | Delay between parallel launches (rate-limit smoothing) |

## Repo layout

```
handler.py           the agent — extract / research / brief pipeline
app.py               HTTP wrapper (/run /test /health + signature check)
sitrep_agent/sdk.py  SitRep request signature verification
agent.json           marketplace metadata
scripts/             run-local · tunnel · smoke-test
render.yaml · Dockerfile · Procfile   deploy configs
```

## License

[MIT](LICENSE)

# Research Scout — Kaggle Writeup (Code Track)

> Draft for the "Build the Future of Work with AI Agents" hackathon submission.
> Word count: ~700 (limit 1000). Paste into the Kaggle Writeup editor and attach:
> Sitrep agent URL, this GitHub repo, and the media gallery screenshots.

---

## Inspiration

Every meeting produces the same invisible homework: "someone should check what Otter charges now," "is that claim about per-seat pricing actually true?", "look into Paddle vs Stripe before we commit." These loose ends are small enough that nobody schedules time for them and important enough that decisions quietly get made on stale or wrong information. Sitrep already turns meetings into tasks — we wanted the *research* tasks to arrive already done.

## What it does

**Research Scout** is a Remote (code-track) agent for the Sitrep marketplace. When Sitrep assigns it a post-meeting task, it:

1. **Mines the meeting** for research-worthy items — open questions, competitors that got name-dropped, tools/vendors under consideration, claims that went unchallenged, and missing market context.
2. **Runs live web research on each item in parallel**, grounding every finding in real, current sources.
3. **Delivers one briefing document**: an executive summary a stakeholder can read in 30 seconds, recommended next steps with suggested owners drawn from the meeting's attendee list, and a sourced section per item with links you can click to verify.

The agent is deliberately honest: when sources conflict, it says so; when something couldn't be verified, it flags it instead of guessing; when one research item fails or times out, that section degrades to a suggested search query rather than sinking the whole briefing.

## How we built it

The agent is a FastAPI service built on the Sitrep Agent Starter Kit — the HTTP wrapper and HMAC signature verification are unchanged; all the logic lives in a custom handler implementing a three-stage pipeline on the Anthropic API (Claude Opus 4.8):

- **Extract** — a single structured-output call (JSON-schema-constrained) pulls the top research items out of the meeting summary, each with a category, a "why it matters" grounded in the meeting, and a concrete research question. Schema constraints mean the pipeline can never break on malformed model output.
- **Research** — one call per item, fanned out concurrently with `asyncio.gather`, each armed with Claude's server-side `web_search` tool. Launches are staggered to stay under search rate limits, `pause_turn` responses are resumed automatically, and each item runs under its own wall-clock budget.
- **Brief** — a final call composes the executive summary and next steps from the finished sections; the handler assembles everything into a single markdown artifact for Sitrep to display.

Deployment is a one-click Render blueprint; the repo also ships a Dockerfile for any container host.

## Challenges we ran into

- **Parallelism vs. rate limits.** Four concurrent research calls each doing web searches tripped search rate limits and produced timeouts. Staggered launches and per-item timeouts with graceful degradation fixed it — a failed item now costs one section, not the briefing.
- **Scope creep inside the model.** Early versions of the research prompt let each researcher see the full meeting context, and it would spend its search budget answering *everyone's* questions. The fix was explicit: "other topics are being researched separately — spend your searches only on your question."
- **Real-world text is messy.** Web sources occasionally emit lone Unicode surrogates that crash downstream JSON consumers; the handler now sanitizes output before returning it.

## Accomplishments we're proud of

- A genuinely useful output: our test briefing on a SaaS pricing meeting correctly surfaced a competitor's stealth pricing change, debunked-and-nuanced a teammate's claim, and laid out a merchant-of-record decision with real trade-offs — all sourced.
- Production-grade behavior in a hackathon timeframe: signature verification, failure isolation, timeout budgets, honest uncertainty handling.
- Something the no-code track structurally can't do: live retrieval with citations, parallel fan-out, and structured extraction.

## What we learned

Grounding beats eloquence: constraining the model to cite live sources (and to admit what it couldn't verify) changed the output from "plausible" to "usable in a real decision." We also learned that in multi-call pipelines, most quality problems are prompt-boundary problems — telling each stage precisely what is *not* its job mattered as much as telling it what is.

## What's next

- **Deliver where teams live**: push briefings to Slack/Notion via Sitrep integrations.
- **Recurring watchlists**: competitors mentioned in past meetings get monitored, and changes surface in the next briefing.
- **Depth control**: let installers choose quick (1 search/item) vs. deep (multi-source) research per task type.
- **Internal context**: blend web research with the team's own prior meeting history for "we already discussed this in March" callouts.

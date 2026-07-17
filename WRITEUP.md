<!-- Kaggle writeup — paste everything below this comment block into the Kaggle
     Writeup editor (~640 words, limit 1000). Attach:
     - Agent URL: https://app.joinsitrep.com/dashboard/marketplace/research-scout--fcac5670-ce8b-4154-ae67-e35cba24ff8f
     - Repo: https://github.com/ns-0437/research-scout
     - Media: screenshots + demo video -->

# Research Scout — sourced research briefings from your meetings

## Inspiration

Every meeting I've been part of ends the same way: someone says "we should look into that" and nobody ever does. A competitor gets mentioned, a number gets quoted that nobody checks, somebody suggests a tool and the tab gets closed by Friday. SitRep already turns meetings into tasks, so the gap felt obvious — the research tasks it creates were still waiting on a human. I wanted them to arrive already done.

## What it does

Research Scout is a Remote agent (Code Track). When SitRep hands it a research task, it reads the meeting summary and pulls out what's worth chasing: open questions, competitors that got name-dropped, tools under consideration, claims nobody could confirm. It researches each one on the live web, in parallel, and returns a single briefing: an executive summary you can read in thirty seconds, next steps with suggested owners taken from the attendee list, and one section per item with the answer, the evidence, and links to sources.

The part I care most about is that it doesn't bluff. If sources disagree, it says so. If it can't verify something, it flags it instead of guessing. In SitRep's own Studio test, the auto-generated meeting never actually named the competitor — and the agent responded that the research couldn't be done as scoped, rather than inventing pricing for a company that doesn't exist. That test passed, and it's honestly my favorite output so far.

## How I built it

It's a FastAPI service on the SitRep starter kit; the HTTP wrapper and HMAC signature check are stock. The pipeline is three stages on Claude (Anthropic API):

1. **Extract** — one structured-output call (JSON schema) turns the meeting into a ranked list of research items, each with a category, why it matters, and a concrete question. Schema constraints mean it never breaks on malformed output.
2. **Research** — one call per item, fanned out with asyncio, each using Claude's server-side web search. Launches are staggered to stay under rate limits, paused turns get resumed, and every item runs on its own timeout.
3. **Brief** — a final call writes the exec summary and next steps, and plain Python stitches the document together.

Deployment is a Render free-tier blueprint (Dockerfile included). Studio "Test" requests hit a quick mode — 2 items, 1 search each, about 30 seconds — so the test button doesn't time out, while real tasks get full depth.

## Challenges I ran into

Parallel research kept tripping web-search rate limits, and two of four sections would time out. Staggered launches plus failure isolation fixed it: a dead item now degrades to a suggested search query instead of sinking the whole briefing. Early on, each researcher could also see the whole meeting and would burn its search budget answering everyone else's questions — the fix was embarrassingly simple: tell it the other topics are being handled separately. And web text is messy; a stray lone surrogate character from a scraped page once broke a downstream JSON consumer, so there's a sanitizer now.

## Accomplishments I'm proud of

My test briefing on a mock pricing meeting caught something real: Otter.ai quietly cut its Pro plan from 6,000 to 1,200 transcription minutes without changing the price — sourced, with a caveat that review blogs disagreed on one figure and it should be confirmed on the official page. That's the bar I wanted: output you could take into your next meeting. I'm also glad the unglamorous parts made it in before the deadline: signature verification, per-item timeouts, refusal handling, honest uncertainty.

## What I learned

Grounding beats eloquence — forcing the model to cite live sources and admit what it couldn't verify moved the output from "sounds right" to "usable in a decision". And in multi-call pipelines, most quality bugs were prompt-boundary bugs: telling each stage what is *not* its job mattered as much as telling it what is.

## What's next

Briefings delivered into Slack and Notion through SitRep's integrations, watchlists for competitors that keep showing up across meetings, a quick-vs-deep research setting per task type, and blending in the team's own meeting history so it can say "you already discussed this in March."

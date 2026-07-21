"""Research Scout — a post-meeting research agent for the SitRep marketplace.

Every meeting leaves behind loose ends nobody has time to chase: open
questions, competitors that got name-dropped, tools someone suggested,
claims that went unchallenged. Research Scout picks up the task, mines the
meeting for those research-worthy items, runs live web research on each one
in parallel, and returns a single sourced briefing document.

Pipeline (three stages, primarily on the Anthropic API):

  1. EXTRACT  — one structured-output call pulls the highest-value research
                items out of the meeting summary (topic, category, why it
                matters, and a concrete research question).
  2. RESEARCH — one call per item, run concurrently, each armed with a
                server-side web search tool. Findings come back grounded
                in live sources with inline links.
  3. BRIEF    — a final call writes the executive summary and recommended
                next steps; the code assembles everything into one
                markdown artifact for SitRep to display.

Each stage runs on Claude by default. If GEMINI_API_KEY is set and the
Claude call fails (e.g. the account is out of credit), that stage
transparently retries once on Gemini so a temporary billing hiccup doesn't
take the whole agent down mid-demo. This is an operational safety net, not
the primary implementation — leave GEMINI_API_KEY unset to run purely on
Claude.

Configuration (env):
  ANTHROPIC_API_KEY    required — your Anthropic API key.
  CLAUDE_MODEL         default "claude-opus-4-8".
  GEMINI_API_KEY        optional — enables the Gemini fallback described above.
  GEMINI_MODEL          default "gemini-2.5-flash".
  MAX_RESEARCH_ITEMS   default 4 — cap on parallel research items.
"""
from __future__ import annotations

import asyncio
import json
import os

from anthropic import AsyncAnthropic

from sitrep_agent.sdk import AgentInput, Ctx

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_ITEMS = int(os.getenv("MAX_RESEARCH_ITEMS", "4"))
SEARCHES_PER_ITEM = int(os.getenv("SEARCHES_PER_ITEM", "3"))
ITEM_TIMEOUT_SECONDS = float(os.getenv("ITEM_TIMEOUT_SECONDS", "210"))
ITEM_STAGGER_SECONDS = float(os.getenv("ITEM_STAGGER_SECONDS", "2.5"))

client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from the environment

_gemini_client = None


def _get_gemini_client():
    """Lazily construct the Gemini fallback client. Returns None if no key is
    configured, or if the SDK can't be loaded — callers treat that as
    "no fallback available" rather than crashing."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai

        _gemini_client = genai.Client(api_key=api_key)
    except Exception:
        return None
    return _gemini_client


CATEGORY_LABELS = {
    "open_question": "Open question",
    "competitor": "Competitor",
    "tool_or_vendor": "Tool / vendor",
    "claim_to_verify": "Claim to verify",
    "market_context": "Market context",
}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Short name for the research item, e.g. a company, tool, or question",
                    },
                    "category": {
                        "type": "string",
                        "enum": list(CATEGORY_LABELS),
                    },
                    "why_it_matters": {
                        "type": "string",
                        "description": "One sentence on why the team needs this answered, grounded in the meeting",
                    },
                    "research_question": {
                        "type": "string",
                        "description": "The concrete question live web research should answer",
                    },
                },
                "required": ["topic", "category", "why_it_matters", "research_question"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

EXTRACTION_SYSTEM = f"""You mine meeting summaries for research-worthy items: unanswered questions, \
competitors or companies mentioned, tools/vendors under consideration, factual claims that should be \
verified, and market context the team is missing. Select only items where live web research would \
genuinely help the team — skip anything the meeting already resolved or that is internal to the company \
(you cannot research their private data). Return at most {MAX_ITEMS} items, ordered by business value. \
If the meeting contains nothing worth researching, return an empty list."""

RESEARCH_SYSTEM = """You are a sharp business research analyst. Answer ONE research question using web \
search, grounding every substantive statement in what you find. The meeting context may mention other \
topics — those are being researched separately in parallel; spend your searches and your answer ONLY on \
the question you were given. Write 2-4 tight paragraphs (or a short list where that reads better): lead \
with the direct answer, then the key evidence, then anything the team should watch out for. Cite \
sources as inline markdown links on the phrases they support. If sources conflict or you cannot verify \
something, say so plainly rather than guessing. Formatting: no headings of any kind and no preamble or \
meta-commentary about your process — your text is inserted under a prepared heading, so start directly \
with the substance."""

BRIEF_SYSTEM = """You write the opening of a post-meeting research briefing. Given the meeting context \
and the completed research sections, write:

1. An **Executive summary** — 3-5 sentences a busy stakeholder can read instead of the whole document. \
Lead with the most decision-relevant finding.
2. **Recommended next steps** — 3-5 short bullets, each a concrete action grounded in the research. \
Where the meeting attendees are known, suggest who is the natural owner.

Return only these two sections in markdown, with exactly those two headings at the ## level. No preamble."""


def _guidance_block(ctx: Ctx) -> str:
    """Installer guidance from the SitRep Studio (agent.instructions), if any."""
    guidance = (ctx.instructions or "").strip()
    return f"\n\nGuidance from the agent's installer (honor it):\n{guidance}" if guidance else ""


def _clean(text: str) -> str:
    """Strip lone surrogates and other unencodable characters from model/web text."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


# ── Stage 1: extract ─────────────────────────────────────────────────────

async def extract_items(task_text: str, summary: str, ctx: Ctx) -> list[dict]:
    """Pull research-worthy items out of the meeting. Claude first, Gemini
    fallback on failure if configured."""
    try:
        return await _extract_items_anthropic(task_text, summary, ctx)
    except Exception as exc:
        gemini = _get_gemini_client()
        if gemini is None:
            raise
        ctx.log(f"Claude extraction failed ({type(exc).__name__}) — retrying on Gemini")
        return await _extract_items_gemini(gemini, task_text, summary, ctx)


async def _extract_items_anthropic(task_text: str, summary: str, ctx: Ctx) -> list[dict]:
    response = await client.messages.create(
        model=MODEL,
        max_tokens=2048,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
        },
        system=EXTRACTION_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Task assigned to you:\n{task_text}\n\nMeeting summary:\n{summary}"
                           + _guidance_block(ctx),
            }
        ],
    )
    text = next((b.text for b in response.content if b.type == "text"), "{}")
    items = json.loads(text).get("items", [])
    return items[:MAX_ITEMS]


async def _extract_items_gemini(gemini_client, task_text: str, summary: str, ctx: Ctx) -> list[dict]:
    from google.genai import types

    item_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "topic": types.Schema(type=types.Type.STRING),
            "category": types.Schema(type=types.Type.STRING, enum=list(CATEGORY_LABELS)),
            "why_it_matters": types.Schema(type=types.Type.STRING),
            "research_question": types.Schema(type=types.Type.STRING),
        },
        required=["topic", "category", "why_it_matters", "research_question"],
    )
    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={"items": types.Schema(type=types.Type.ARRAY, items=item_schema)},
        required=["items"],
    )
    prompt = f"Task assigned to you:\n{task_text}\n\nMeeting summary:\n{summary}" + _guidance_block(ctx)
    response = await gemini_client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=EXTRACTION_SYSTEM,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    items = json.loads(response.text or "{}").get("items", [])
    return items[:MAX_ITEMS]


# ── Stage 2: research ────────────────────────────────────────────────────

def _collect_text_and_sources(content) -> tuple[str, list[tuple[str, str]]]:
    """Concatenate Claude's text blocks and gather any (title, url) citations.

    Text emitted before the final search result is the model narrating its
    search plan, not the answer — keep only what comes after it.
    """
    blocks = list(content)
    last_tool_idx = max(
        (i for i, b in enumerate(blocks) if b.type in ("server_tool_use", "web_search_tool_result")),
        default=-1,
    )
    parts: list[str] = []
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for block in blocks[last_tool_idx + 1:]:
        if block.type == "text":
            parts.append(block.text)
            for citation in getattr(block, "citations", None) or []:
                url = getattr(citation, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append((getattr(citation, "title", None) or url, url))
    return "".join(parts), sources


async def research_item(item: dict, summary: str, ctx: Ctx,
                        max_searches: int = SEARCHES_PER_ITEM) -> str:
    """Live web research on a single item. Returns a markdown section.
    Never raises — a failed item degrades to a suggested search query so it
    can't sink the rest of the briefing."""
    label = CATEGORY_LABELS.get(item["category"], "Research item")
    heading = f"## {item['topic']}\n\n*{label}* — {item['why_it_matters']}\n"

    try:
        return await _research_item_anthropic(item, summary, ctx, max_searches, heading)
    except Exception as exc:
        gemini = _get_gemini_client()
        if gemini is not None:
            try:
                ctx.log(f"Claude research failed for '{item['topic']}' "
                        f"({type(exc).__name__}) — retrying on Gemini")
                return await _research_item_gemini(gemini, item, summary, ctx, heading)
            except Exception as exc2:
                exc = exc2
        ctx.log(f"research failed for {item['topic']}: {exc}")
        return heading + (
            f"\n_Automated research on this item did not complete ({type(exc).__name__}). "
            f"Suggested starting point: search for “{item['research_question']}”._\n"
        )


async def _research_item_anthropic(item: dict, summary: str, ctx: Ctx,
                                   max_searches: int, heading: str) -> str:
    messages = [
        {
            "role": "user",
            "content": (
                f"Research question: {item['research_question']}\n\n"
                f"Meeting context (for relevance, not as a source):\n{summary}"
                + _guidance_block(ctx)
            ),
        }
    ]

    while True:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=6000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            tools=[
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": max_searches,
                }
            ],
            system=RESEARCH_SYSTEM,
            messages=messages,
        )
        if response.stop_reason == "pause_turn":
            # Server-side search loop paused mid-turn; resume where it left off.
            messages = messages[:1] + [{"role": "assistant", "content": response.content}]
            continue
        break

    if response.stop_reason == "refusal":
        return heading + "\n_Research on this item was declined by the model's safety system._\n"

    body, sources = _collect_text_and_sources(response.content)
    section = heading + "\n" + body.strip() + "\n"
    if sources:
        section += "\n**Sources**\n" + "\n".join(f"- [{t}]({u})" for t, u in sources) + "\n"
    ctx.log(f"researched: {item['topic']}")
    return section


async def _research_item_gemini(gemini_client, item: dict, summary: str, ctx: Ctx,
                                heading: str) -> str:
    from google.genai import types

    prompt = (
        f"Research question: {item['research_question']}\n\n"
        f"Meeting context (for relevance, not as a source):\n{summary}"
        + _guidance_block(ctx)
    )
    response = await gemini_client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=RESEARCH_SYSTEM,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    body = (response.text or "").strip()

    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            url = getattr(web, "uri", None) if web else None
            if url and url not in seen:
                seen.add(url)
                sources.append((getattr(web, "title", None) or url, url))
    except Exception:
        pass  # grounding metadata is best-effort — an empty sources list is fine

    section = heading + "\n" + body + "\n"
    if sources:
        section += "\n**Sources**\n" + "\n".join(f"- [{t}]({u})" for t, u in sources) + "\n"
    ctx.log(f"researched via Gemini fallback: {item['topic']}")
    return section


# ── Stage 3: brief ───────────────────────────────────────────────────────

async def write_brief(task_text: str, summary: str, attendees: list[dict],
                      sections: list[str]) -> str:
    try:
        return await _write_brief_anthropic(task_text, summary, attendees, sections)
    except Exception as exc:
        gemini = _get_gemini_client()
        if gemini is None:
            raise
        ctx_log_note = f"Claude brief generation failed ({type(exc).__name__}) — retrying on Gemini"
        return await _write_brief_gemini(gemini, task_text, summary, attendees, sections, ctx_log_note)


async def _write_brief_anthropic(task_text: str, summary: str, attendees: list[dict],
                                 sections: list[str]) -> str:
    names = ", ".join(a.get("name", "") for a in attendees if a.get("name")) or "(not provided)"
    response = await client.messages.create(
        model=MODEL,
        max_tokens=2048,
        output_config={"effort": "low"},
        system=BRIEF_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Task: {task_text}\n\nMeeting summary:\n{summary}\n\n"
                    f"Attendees: {names}\n\nCompleted research sections:\n\n"
                    + "\n\n---\n\n".join(sections)
                ),
            }
        ],
    )
    return next((b.text for b in response.content if b.type == "text"), "").strip()


async def _write_brief_gemini(gemini_client, task_text: str, summary: str, attendees: list[dict],
                              sections: list[str], log_note: str) -> str:
    from google.genai import types

    names = ", ".join(a.get("name", "") for a in attendees if a.get("name")) or "(not provided)"
    prompt = (
        f"Task: {task_text}\n\nMeeting summary:\n{summary}\n\n"
        f"Attendees: {names}\n\nCompleted research sections:\n\n"
        + "\n\n---\n\n".join(sections)
    )
    response = await gemini_client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=BRIEF_SYSTEM),
    )
    return (response.text or "").strip()


# ── Entry point ──────────────────────────────────────────────────────────

async def handler(input: AgentInput, ctx: Ctx) -> dict:
    """Public entry point. Never raises — any failure degrades to a clear artifact
    so the SitRep marketplace shows a helpful message instead of a 500."""
    title = (input.task.get("title") or "Post-meeting research")
    try:
        return await _run_pipeline(input, ctx)
    except Exception as exc:  # noqa: BLE001 — a marketplace agent must not 500
        ctx.log(f"pipeline error: {type(exc).__name__}: {exc}")
        detail = str(exc)
        if "credit balance" in detail.lower() or "billing" in detail.lower():
            note = ("The agent's language-model account is out of credit, so it couldn't run "
                    "research this time. The agent owner needs to top up their API balance; "
                    "please try again afterwards.")
        else:
            note = ("Research didn't complete because of a temporary problem reaching the "
                    "language model. Please try running this task again in a few minutes.")
        return {
            "artifacts": [
                {
                    "type": "markdown",
                    "title": f"Research briefing — {title}",
                    "content": f"## Couldn't complete research\n\n{note}",
                }
            ]
        }


async def _run_pipeline(input: AgentInput, ctx: Ctx) -> dict:
    task = input.task
    title = task.get("title") or "Post-meeting research"
    task_text = title + (f"\n{task['description']}" if task.get("description") else "")

    # Studio "Test" runs have a short timeout — trade depth for speed there.
    quick = input.agent.get("_route") == "test"
    max_items = 2 if quick else MAX_ITEMS
    max_searches = 1 if quick else SEARCHES_PER_ITEM

    ctx.log(f"extracting research items (model={MODEL}{', quick test mode' if quick else ''})")
    items = (await extract_items(task_text, input.summary, ctx))[:max_items]

    if not items:
        return {
            "artifacts": [
                {
                    "type": "markdown",
                    "title": f"Research briefing — {title}",
                    "content": (
                        "## Nothing to research\n\nThis meeting didn't surface open questions, "
                        "competitors, tools, or claims that live web research would help with. "
                        "If you expected research, add more detail to the task description and re-run."
                    ),
                }
            ]
        }

    ctx.log(
        f"researching {len(items)} item(s) in parallel: "
        + ", ".join(i["topic"] for i in items)
    )

    async def guarded(item: dict, delay: float) -> str:
        # Stagger launches so the parallel burst doesn't trip web-search rate limits.
        await asyncio.sleep(delay)
        try:
            return await asyncio.wait_for(
                research_item(item, input.summary, ctx, max_searches),
                timeout=90 if quick else ITEM_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            ctx.log(f"research timed out for {item['topic']}")
            return (
                f"## {item['topic']}\n\n_Research timed out. Suggested starting point: "
                f"search for “{item['research_question']}”._\n"
            )

    sections = await asyncio.gather(
        *(guarded(item, i * ITEM_STAGGER_SECONDS) for i, item in enumerate(items))
    )

    ctx.log("writing executive summary")
    try:
        brief = await write_brief(task_text, input.summary, input.attendees, list(sections))
    except Exception as exc:
        ctx.log(f"brief generation failed: {exc}")
        brief = ""

    document = f"# Research briefing — {title}\n\n"
    if brief:
        document += brief + "\n\n---\n\n"
    document += "\n\n".join(sections)
    document += (
        "\n\n---\n\n_Compiled by Research Scout from live web sources. "
        "Verify time-sensitive figures before acting on them._"
    )
    document = _clean(document)

    return {
        "artifacts": [
            {"type": "markdown", "title": f"Research briefing — {title}", "content": document}
        ]
    }

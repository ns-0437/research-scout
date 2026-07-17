"""HTTP wrapper around your handler. You normally don't edit this.

Exposes the SitRep agent contract:
  GET  /health  -> {"ok": true}
  POST /run     -> runs your handler on a real assignment
  POST /test    -> identical shape; used by the Studio "Test" button

Both /run and /test verify the SitRep request signature (see sdk.verify_signature).
"""
from __future__ import annotations

from fastapi import FastAPI, Request, Response

from handler import handler
from sitrep_agent.sdk import MODEL, AgentInput, Ctx, LLM, verify_signature

app = FastAPI(title="Research Scout")


@app.get("/")
async def root():
    return {
        "agent": "Research Scout",
        "tagline": "Turns your meeting's open questions, competitors, and unverified claims "
                   "into a sourced research briefing.",
        "marketplace": "SitRep (https://joinsitrep.com)",
        "source": "https://github.com/ns-0437/research-scout",
        "endpoints": {"GET /health": "liveness check",
                      "POST /run": "SitRep task webhook (HMAC-verified)",
                      "POST /test": "SitRep Studio test button (HMAC-verified)"},
    }


@app.get("/health")
async def health():
    return {"ok": True}


async def _handle(request: Request) -> Response | dict:
    body = await request.body()
    if not verify_signature(
        request.headers.get("X-SitRep-Timestamp"),
        request.headers.get("X-SitRep-Signature"),
        body,
    ):
        return Response(status_code=401, content='{"error":"bad signature"}',
                        media_type="application/json")

    import json

    payload = json.loads(body or b"{}")
    agent_input = AgentInput.from_payload(payload)
    # A remote agent runs on ITS OWN LLM (your MODEL env) — not whatever model
    # name SitRep happens to send (that may be a cloud name your Ollama lacks).
    # `agent_input.agent.get("model")` is still available if you want to honor it.
    ctx = Ctx(
        instructions=agent_input.agent.get("instructions", ""),
        tools=agent_input.agent.get("tools", []),
        llm=LLM(MODEL),
    )
    result = await handler(agent_input, ctx)
    artifacts = result.get("artifacts", []) if isinstance(result, dict) else []
    return {"artifacts": artifacts, "logs": ctx.logs}


@app.post("/run")
async def run(request: Request):
    return await _handle(request)


@app.post("/test")
async def test(request: Request):
    return await _handle(request)

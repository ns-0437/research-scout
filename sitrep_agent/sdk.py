"""SitRep Agent SDK — request signature verification + a tiny LLM client.

You normally don't edit this file. It gives you:

  * verify_signature(...)  — confirm a /run or /test request really came from
    SitRep (HMAC-SHA256 over "<timestamp>.<body>" using your agent secret).
  * LLM                    — an OpenAI-compatible chat client that defaults to a
    local Ollama, or any BYOK provider via env vars.
  * Ctx / AgentInput       — the objects passed to your handler().
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# ── Config (env) ─────────────────────────────────────────────────────
SITREP_AGENT_SECRET = os.getenv("SITREP_AGENT_SECRET", "")
SIGNATURE_MAX_AGE_SECONDS = int(os.getenv("SITREP_SIGNATURE_MAX_AGE", "300"))
# LLM: defaults to local Ollama (free, no signup). BYOK by overriding these.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")  # Ollama ignores; required for hosted providers.
MODEL = os.getenv("MODEL", "llama3.2:1b")


def verify_signature(timestamp: str | None, signature: str | None, body: bytes) -> bool:
    """Return True iff the request is a fresh, correctly-signed SitRep call.

    If SITREP_AGENT_SECRET is unset (pure local dev) this returns True so you can
    iterate without wiring a secret. Set the secret in production.
    """
    if not SITREP_AGENT_SECRET:
        return True
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > SIGNATURE_MAX_AGE_SECONDS:
            return False  # replay guard
    except ValueError:
        return False
    expected = "sha256=" + hmac.new(
        SITREP_AGENT_SECRET.encode(),
        msg=f"{timestamp}.".encode() + body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


class LLM:
    """Minimal OpenAI-compatible chat client (works with Ollama, OpenAI,
    OpenRouter, vLLM, LM Studio, …)."""

    def __init__(self, model: str):
        self.model = model

    async def complete(self, system: str, prompt: str, temperature: float = 0.7) -> str:
        url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={
                    "model": self.model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


@dataclass
class Ctx:
    instructions: str
    tools: list[str]
    llm: LLM
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.logs.append(message)


@dataclass
class AgentInput:
    task: dict[str, Any]
    summary: str
    attendees: list[dict[str, Any]]
    agent: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict) -> "AgentInput":
        return cls(
            task=payload.get("task") or {},
            summary=payload.get("summary") or "",
            attendees=payload.get("attendees") or [],
            agent=payload.get("agent") or {},
        )

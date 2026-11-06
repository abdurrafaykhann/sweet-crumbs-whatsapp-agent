"""Model factory and helpers. M3 adds provider fallback, retries with backoff and usage tracking on top of this."""
from __future__ import annotations

import os

DEFAULT_PRIMARY = "groq:openai/gpt-oss-120b"


def get_model(spec: str | None = None, **kwargs):
    """A LangChain chat model from a 'provider:model' string, e.g. groq:openai/gpt-oss-120b or
    google_genai:gemini-flash-lite-latest. Provider and model come from .env (LLM_PRIMARY), never from code."""
    from langchain.chat_models import init_chat_model
    spec = spec or os.environ.get("LLM_PRIMARY") or DEFAULT_PRIMARY
    kwargs.setdefault("temperature", 0.3)
    kwargs.setdefault("max_retries", 2)
    return init_chat_model(spec, **kwargs)


def message_text(msg) -> str:
    """Plain text of a model reply. Groq returns a string; Gemini returns a list of content blocks."""
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts).strip()

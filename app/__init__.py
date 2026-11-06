"""Load KEY=VALUE lines from a .env file in the project root (never overrides real environment variables)."""
import os

_p = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_p):
    for _l in open(_p, encoding="utf-8"):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Provider shorthands: set GEMINI_API_KEY or GROQ_API_KEY (LLM_PROVIDER=gemini|groq to force one; Gemini wins if both are set)
_prov = os.environ.get("LLM_PROVIDER") or ("gemini" if os.environ.get("GEMINI_API_KEY") else "groq" if os.environ.get("GROQ_API_KEY") else "")
if not os.environ.get("LLM_BASE_URL"):
    if _prov == "gemini" and os.environ.get("GEMINI_API_KEY"):
        os.environ["LLM_BASE_URL"] = "https://generativelanguage.googleapis.com/v1beta/openai"
        os.environ.setdefault("LLM_MODEL", "gemini-3.5-flash")
        os.environ.setdefault("LLM_REASONING_EFFORT", "low")
        os.environ.setdefault("LLM_API_KEY", os.environ["GEMINI_API_KEY"])
    elif _prov == "groq" and os.environ.get("GROQ_API_KEY"):
        os.environ["LLM_BASE_URL"] = "https://api.groq.com/openai/v1"
        os.environ.setdefault("LLM_MODEL", "openai/gpt-oss-120b")
        os.environ.setdefault("LLM_API_KEY", os.environ["GROQ_API_KEY"])

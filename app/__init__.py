"""Load KEY=VALUE lines from a .env file in the project root (never overrides real environment variables)."""
import os

_p = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_p):
    for _l in open(_p, encoding="utf-8"):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

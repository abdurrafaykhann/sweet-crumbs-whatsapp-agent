"""Vercel entry point: the FastAPI app lives in app/main.py."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402,F401

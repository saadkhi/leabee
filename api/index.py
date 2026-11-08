"""Vercel serverless function: exposes the Flask `app` (all routes are rewritten here by vercel.json)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leabee.serverless import app  # noqa: E402,F401

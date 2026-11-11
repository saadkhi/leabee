"""Leabee Copilot: a chat assistant for YOUR pipeline, built on Parlant (https://github.com/emcie-co/parlant).

Parlant runs as its own server (`python -m leabee copilot`, port 8800) and keeps the agent inside the policies in
`spec.py`. The dashboard talks to it through /api/copilot/ask. It needs `pip install parlant` and
OPENROUTER_API_KEY (free models work). It only ever reads leads, writes drafts and (after you confirm) changes a lead's status; it never sends messages."""

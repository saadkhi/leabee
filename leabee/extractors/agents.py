"""AI browser-agent backend: Browser Use (open source, MIT). It drives a real local browser with an LLM of your choice.

Free setup: `pip install 'browser-use==1.0.0'`, then either OPENROUTER_API_KEY (free ":free" models) or a local Ollama
(LEABEE_LLM_BASE_URL=http://localhost:11434/v1, LEABEE_LLM_MODEL=llama3.2). Last-resort tool: slower and less reliable than plain
fetch or Crawl4AI, so it is opt-in (`extractors.use_agents`). It never logs in, submits forms or sends messages.

Interface: backend.extract(url, goal, fields) -> {field_name: value}. Written against Browser Use's documented API
(Agent, Browser, ChatOpenAI(model, base_url, api_key)); unit-tested with a fake SDK, not run live here.
(AgentQL, Skyvern and Stagehand adapters were removed: they need paid or hosted API keys.)
"""
from __future__ import annotations

import asyncio
import json
import re
import threading

from .. import llm
from .base import is_public_url

CONTACT_FIELDS = {
    "emails": "business email addresses found on the page (list of strings)",
    "phones": "phone numbers (list of strings)",
    "owner_or_contact_name": "name of the owner, founder or best person to contact",
    "linkedin": "LinkedIn profile or company URL",
    "services": "one short sentence on what the business does",
}


def _json_from(text: str):
    """Pull the first JSON object/array out of an LLM answer."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    blob = m.group(1) if m else text
    m = re.search(r"[\[{].*[\]}]", blob, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}

    def target():
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as e:      # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["v"]


def _task(url: str, goal: str, fields: dict) -> str:
    spec = "\n".join(f'- "{k}": {v}' for k, v in fields.items())
    return (f"Open {url} and {goal}.\nReturn ONLY a JSON object with these keys (use null or [] when not found, never invent data):\n{spec}\n"
            "Do not log in, do not submit any form, do not send any message.")


class BrowserUseBackend:
    name = "browser_use"

    def available(self) -> tuple[bool, str]:
        try:
            import browser_use  # noqa: F401
        except ImportError:
            return False, "pip install 'browser-use==1.0.0'"
        if not llm.available():
            return False, "set OPENROUTER_API_KEY (free) or LEABEE_LLM_BASE_URL for a local Ollama"
        return True, ""

    def extract(self, url: str, goal: str, fields: dict, timeout: float = 240) -> dict:
        if not is_public_url(url):
            return {}
        from browser_use import Agent, Browser, ChatOpenAI

        s = llm.settings()

        async def go():
            model = ChatOpenAI(s["models"][0], base_url=s["base_url"], api_key=s["api_key"])
            agent = Agent(_task(url, goal, fields), llm=model, browser=Browser("chromium"))
            result = await asyncio.wait_for(agent.run(), timeout)
            return result.final_result()
        data = _json_from(str(_run_async(go()) or ""))
        return data if isinstance(data, dict) else {}

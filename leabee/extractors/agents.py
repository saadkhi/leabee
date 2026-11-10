"""AI browser-agent backends: AgentQL, Browser Use, Skyvern, Stagehand.

These are the expensive, last-resort tools: use them where a plain fetch or Crawl4AI cannot (login-gated or
form-driven pages, heavily dynamic sites, "find the contact person" style goals). Each needs its own key and is OFF until
you configure it; `python -m leabee tools` shows what is ready. All four share one interface:

    backend.extract(url, goal, fields) -> {field_name: value}      (fields = {"emails": "business email addresses", ...})

Adapters follow each project's documented Python API (checked against the READMEs on PyPI). They have unit tests with fake
SDKs, but they could not be run live without your API keys.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading

import requests

from .base import is_public_url

CONTACT_FIELDS = {
    "emails": "business email addresses found on the page (list of strings)",
    "phones": "phone numbers (list of strings)",
    "owner_or_contact_name": "name of the owner, founder or best person to contact",
    "linkedin": "LinkedIn profile or company URL",
    "services": "one short sentence on what the business does",
}
DEFAULT_MODEL = "claude-sonnet-5-5"


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


class AgentQLBackend:
    """AgentQL (TinyFish): semantic queries over a page via REST. Needs AGENTQL_API_KEY (dev.agentql.com)."""
    name = "agentql"
    API = "https://api.agentql.com/v1/query-data"

    def available(self) -> tuple[bool, str]:
        return (True, "") if os.environ.get("AGENTQL_API_KEY") else (False, "set AGENTQL_API_KEY")

    def extract(self, url: str, goal: str, fields: dict, timeout: float = 90) -> dict:
        if not is_public_url(url):
            return {}
        body = "\n".join(f"  {k}[]" if any(w in v for w in ("list", "addresses", "numbers")) else f"  {k}" for k, v in fields.items())
        r = requests.post(self.API, timeout=timeout, headers={"X-API-Key": os.environ["AGENTQL_API_KEY"], "Content-Type": "application/json"},
                          json={"query": "{\n" + body + "\n}", "url": url, "params": {"mode": "fast", "wait_for": 1}})
        if r.status_code != 200:
            raise RuntimeError(f"AgentQL HTTP {r.status_code}: {r.text[:160]}")
        return r.json().get("data") or {}


class BrowserUseBackend:
    """Browser Use (pip install 'browser-use==1.0.0'): an LLM drives a real browser. Needs ANTHROPIC_API_KEY (or BROWSER_USE_API_KEY)."""
    name = "browser_use"

    def available(self) -> tuple[bool, str]:
        try:
            import browser_use  # noqa: F401
        except ImportError:
            return False, "pip install 'browser-use==1.0.0'"
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("BROWSER_USE_API_KEY")):
            return False, "set ANTHROPIC_API_KEY (or BROWSER_USE_API_KEY)"
        return True, ""

    def extract(self, url: str, goal: str, fields: dict, timeout: float = 240) -> dict:
        if not is_public_url(url):
            return {}
        from browser_use import Agent, Browser

        kwargs = {}
        if os.environ.get("ANTHROPIC_API_KEY"):
            from browser_use import ChatAnthropic
            kwargs["llm"] = ChatAnthropic(os.environ.get("LEABEE_AGENT_MODEL", DEFAULT_MODEL))
            kwargs["browser"] = Browser("chromium")

        async def go():
            agent = Agent(_task(url, goal, fields), **kwargs)
            result = await asyncio.wait_for(agent.run(), timeout)
            return result.final_result()
        data = _json_from(str(_run_async(go()) or ""))
        return data if isinstance(data, dict) else {}


class SkyvernBackend:
    """Skyvern (pip install skyvern): vision-driven workflows, good for forms/portals. Needs SKYVERN_API_KEY (cloud) or a local server."""
    name = "skyvern"

    def available(self) -> tuple[bool, str]:
        try:
            import skyvern  # noqa: F401
        except ImportError:
            return False, "pip install skyvern"
        return (True, "") if os.environ.get("SKYVERN_API_KEY") else (False, "set SKYVERN_API_KEY (or run `skyvern quickstart` locally)")

    def extract(self, url: str, goal: str, fields: dict, timeout: float = 300) -> dict:
        if not is_public_url(url):
            return {}
        from skyvern import Skyvern

        schema = {"type": "object", "properties": {k: {"type": "string" if "list" not in v else "array", "description": v} for k, v in fields.items()}}

        async def go():
            client = Skyvern(api_key=os.environ["SKYVERN_API_KEY"])
            task = await asyncio.wait_for(client.run_task(
                prompt=_task(url, goal, fields), url=url, data_extraction_schema=schema), timeout)
            return getattr(task, "output", None)
        out = _run_async(go())
        if isinstance(out, str):
            out = _json_from(out)
        return out if isinstance(out, dict) else {}


class StagehandBackend:
    """Stagehand Python SDK (pip install stagehand): natural-language extract() on a local or Browserbase browser. Needs ANTHROPIC_API_KEY."""
    name = "stagehand"

    def available(self) -> tuple[bool, str]:
        try:
            import stagehand  # noqa: F401
        except ImportError:
            return False, "pip install stagehand"
        return (True, "") if os.environ.get("ANTHROPIC_API_KEY") else (False, "set ANTHROPIC_API_KEY")

    def extract(self, url: str, goal: str, fields: dict, timeout: float = 240) -> dict:
        if not is_public_url(url):
            return {}
        from pydantic import create_model
        from stagehand import Stagehand, local_browser

        Model = create_model("Extracted", **{k: (list[str] | str | None, None) for k in fields})

        async def go():
            browser = await local_browser.launch(headless=True)
            try:
                sh = await Stagehand.create(browser=browser, model="anthropic/" + os.environ.get("LEABEE_AGENT_MODEL", DEFAULT_MODEL),
                                            model_api_key=os.environ["ANTHROPIC_API_KEY"])
                try:
                    page = (await browser.context.pages())[0]
                    await page.goto(url)
                    res = await asyncio.wait_for(sh.extract(f"{goal}. " + "; ".join(f"{k}: {v}" for k, v in fields.items()), Model), timeout)
                    return res.data.model_dump() if hasattr(res.data, "model_dump") else dict(res.data)
                finally:
                    await sh.close()
            finally:
                await browser.close()
        out = _run_async(go())
        return out if isinstance(out, dict) else {}

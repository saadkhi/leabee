"""One LLM client for the whole app: OpenRouter (https://openrouter.ai) or ANY OpenAI-compatible server, including free local Ollama.

Free options:
  * OpenRouter free models: set OPENROUTER_API_KEY (a free account is enough) and keep the default ":free" models.
  * Fully local and open source: install Ollama, `ollama pull llama3.2`, then set
        LEABEE_LLM_BASE_URL=http://localhost:11434/v1   LEABEE_LLM_MODEL=llama3.2
    (no key needed).

Nothing in Leabee depends on an LLM to work: without one, AI review, AI replies and the chat parser fall back to rules and templates.
"""
from __future__ import annotations

import json
import os
import re

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1"
# Free OpenRouter models change over time; override with LEABEE_LLM_MODEL (comma list tried in order).
DEFAULT_FREE_MODELS = ["meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-chat-v3-0324:free", "google/gemma-3-27b-it:free"]


class LLMError(RuntimeError):
    pass


def settings() -> dict:
    base = os.environ.get("LEABEE_LLM_BASE_URL", "").strip().rstrip("/")
    key = os.environ.get("LEABEE_LLM_API_KEY", "").strip() or os.environ.get("OPENROUTER_API_KEY", "").strip()
    models = [m.strip() for m in os.environ.get("LEABEE_LLM_MODEL", "").split(",") if m.strip()]
    if not base:
        base = OPENROUTER_URL
        models = models or DEFAULT_FREE_MODELS
    elif not key:
        key = "local"                                   # Ollama / LM Studio / vLLM ignore the key
    return {"base_url": base, "api_key": key, "models": models or ["llama3.2"], "openrouter": base == OPENROUTER_URL}


def available() -> bool:
    s = settings()
    return bool(s["api_key"])


def describe() -> str:
    s = settings()
    return f"{'OpenRouter' if s['openrouter'] else s['base_url']} ({', '.join(s['models'][:2])})" if s["api_key"] else "off (set OPENROUTER_API_KEY or LEABEE_LLM_BASE_URL)"


def complete(prompt: str, system: str = "", max_tokens: int = 900, temperature: float = 0.3, timeout: float = 90) -> str:
    """Return the model's text. Tries each configured model until one answers; raises LLMError when none does."""
    s = settings()
    if not s["api_key"]:
        raise LLMError("no LLM configured")
    headers = {"Authorization": f"Bearer {s['api_key']}", "Content-Type": "application/json"}
    if s["openrouter"]:
        headers.update({"HTTP-Referer": "https://github.com/saadkhi/leabee", "X-Title": "Leabee"})
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    last = ""
    for model in s["models"]:
        try:
            r = requests.post(f"{s['base_url']}/chat/completions", headers=headers, timeout=timeout,
                              json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature})
        except requests.RequestException as e:
            last = f"{e.__class__.__name__}"
            continue
        if r.status_code != 200:
            last = f"{model}: HTTP {r.status_code}"
            continue
        try:
            text = r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError):
            last = f"{model}: bad response"
            continue
        if text.strip():
            return text
        last = f"{model}: empty"
    raise LLMError(last or "no model answered")


def complete_json(prompt: str, system: str = "", **kw):
    """Like complete() but parses the first JSON object/array in the answer. Returns None when it is not valid JSON."""
    try:
        text = complete(prompt, system, **kw)
    except LLMError:
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

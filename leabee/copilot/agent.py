"""Run the Parlant server with the Leabee copilot: `python -m leabee copilot` (needs parlant[anthropic] + ANTHROPIC_API_KEY).

Written against the Parlant 4.x SDK documented in its README (p.Server, create_agent, create_policy, @p.tool, p.ToolResult).
Not run live here (it needs an LLM key); the tool functions and the policies are unit-tested separately."""
from __future__ import annotations

import asyncio

from ..config import load_config
from ..storage import open_store
from . import tools as T
from .spec import AGENT_ID, INSTRUCTIONS, NAME, POLICIES


async def serve(port: int = 8800) -> None:
    try:
        import parlant.sdk as p
    except ImportError as e:
        raise SystemExit('Install the copilot dependency first:  pip install "parlant[anthropic]"') from e

    cfg = load_config()
    store = open_store(cfg)

    @p.tool
    async def search_leads(query: str = "", category: str = "", country: str = "", min_grade: str = "C", kind: str = "", limit: int = 5) -> p.ToolResult:
        """Search new leads. min_grade is A, B, C or D; kind is gig or prospect; category like automation or web_development."""
        return p.ToolResult(data=T.search_leads(store, query, category, country, min_grade, kind, limit))

    @p.tool
    async def lead_details(lead_id: str) -> p.ToolResult:
        """Full details and the score breakdown for one lead (use the short id)."""
        return p.ToolResult(data=T.lead_details(store, lead_id))

    @p.tool
    async def draft_reply(lead_id: str) -> p.ToolResult:
        """Write a draft reply (short DM, email, follow-up) for a lead. Draft only, never sent."""
        return p.ToolResult(data=T.draft_reply(store, cfg, lead_id))

    @p.tool
    async def set_lead_status(lead_id: str, status: str, follow_up_days: int = 0) -> p.ToolResult:
        """Set a lead's status (new, contacted, replied, won, lost, ignored), optionally with a follow-up in N days. Only after the user confirms."""
        return p.ToolResult(data=T.set_lead_status(store, lead_id, status, follow_up_days))

    @p.tool
    async def pipeline_summary() -> p.ToolResult:
        """Counts, statuses, top categories and follow-ups that are due."""
        return p.ToolResult(data=T.pipeline_summary(store))

    registry = {"search_leads": search_leads, "lead_details": lead_details, "draft_reply": draft_reply,
                "set_lead_status": set_lead_status, "pipeline_summary": pipeline_summary}
    async with p.Server(port=port, models={"*": "anthropic"}) as server:
        agent = await server.create_agent(id=AGENT_ID, name=NAME, instructions=INSTRUCTIONS)
        for title, content, tool_names in POLICIES:
            await agent.create_policy(title=title, content=content, tools=[registry[n] for n in tool_names])


def run(port: int = 8800) -> None:
    asyncio.run(serve(port))

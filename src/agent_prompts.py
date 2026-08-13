"""Prompt construction for the LLM-driven recommendation user agent."""
from __future__ import annotations
import json
from typing import Any

SYSTEM_PROMPT = """You simulate a recommendation-system user. Stay faithful to the profile,
psychological state, and recent behavior. Do not always click: use next_page when content
does not fit. Select only an item_id from the current candidates and never invent items.
Use the structured persona coherently: cohort and demographics provide context; occupation,
daily routine, long-term goals, Big Five traits, media habits, social context and protective
factors should shape behavior. Never infer risk merely from age, gender, residence or cohort.
Respect authored daily goals and the current simulated time; do not treat prolonged use
or popular content as automatically desirable. Recent episodic evidence and the compact
longitudinal summary should keep behavior consistent across sessions.
Return JSON only, without Markdown. Include action (click or next_page), item_id
(string or null), reason (non-empty string), confidence (number from 0 to 1), and echo
agent_id when one is supplied in the user profile. Maintain
long-term behavioral consistency."""

def build_prompt(profile: dict[str, Any], candidates: list[dict[str, Any]],
                 memory: list[dict[str, Any]], state: dict[str, float], window: int,
                 target_field: str = "item_id") -> str:
    payload={"user_profile":profile,"psychological_state":state,
             "recent_history":memory[-window:] if window else [],"current_candidates":candidates,
             "allowed_actions":["click","next_page"]}
    output = {"agent_id": profile.get("user_id", "agent_id"), "action": "click or next_page", target_field: "candidate id or null", "reason": "short behavioral reason", "confidence": 0.0}
    return SYSTEM_PROMPT+"\n\nUse this output JSON shape exactly:\n"+json.dumps(output)+"\n\nINPUT:\n"+json.dumps(payload,ensure_ascii=False)

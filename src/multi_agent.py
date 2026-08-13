"""Async multi-user orchestration for independent WebSim browser sessions.

The WebSim Flask service is the shared environment. Each agent receives its own
browser context (and therefore its own WebSim session cookie), profile, memory,
psychological state, and decision history.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .mini_agent import ROOT, UserProfile, resolve_path
    from .websim_agent import run as run_web_agent
except ImportError:
    from mini_agent import ROOT, UserProfile, resolve_path
    from websim_agent import run as run_web_agent


def load_profiles(path: Path, agent_num: int) -> list[UserProfile]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Profiles file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid profiles JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("Profiles JSON must contain a list")
    profiles = [UserProfile.from_dict(item) for item in raw if isinstance(item, dict)]
    if agent_num < 1:
        raise ValueError("agent_num must be at least 1")
    if len(profiles) < agent_num:
        raise ValueError(f"Profiles file contains {len(profiles)} profiles, but {agent_num} agents were requested")
    ids = [profile.user_id for profile in profiles[:agent_num]]
    if len(ids) != len(set(ids)):
        raise ValueError("Each selected profile must have a unique user_id")
    return profiles[:agent_num]


def build_agent_args(args: argparse.Namespace, profile_path: Path, output_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        policy=args.policy, track=args.track, profile_path=str(profile_path), websim_url=args.websim_url,
        output_dir=str(output_dir), model_name=args.model_name, base_url=args.base_url,
        request_timeout=args.request_timeout, max_retries=args.max_retries,
        memory_window=args.memory_window, decision_threshold=args.decision_threshold,
        disable_fallback=args.disable_fallback, headless=args.headless,
        save_screenshots=args.save_screenshots, api_key=args.api_key,
    )


def analyze_run(run_dir: Path) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    for agent_dir in sorted(path for path in run_dir.iterdir() if path.is_dir() and path.name.startswith("agent_")):
        memory_path = agent_dir / "memory.json"
        if not memory_path.exists():
            continue
        data = json.loads(memory_path.read_text(encoding="utf-8"))
        trajectory = data.get("trajectory", [])
        profile = data.get("profile", {})
        clicks = [row.get("llm_decision", {}).get("item_id") for row in trajectory if row.get("executed_action") == "click"]
        initial = trajectory[0].get("psychological_state_before", {}) if trajectory else {}
        final = data.get("final_psychological_state", {})
        candidate_ids = {item.get("item_id") for row in trajectory for item in row.get("observation", {}).get("candidates", [])}
        agents.append({
            "agent_id": profile.get("user_id", agent_dir.name),
            "personality": profile.get("personality", "balanced"),
            "interests": profile.get("interests", []),
            "session_length": len(trajectory),
            "interaction_count": len(trajectory),
            "clicked_item_sequence": clicks,
            "click_count": len(clicks),
            "recommendation_influence": {"unique_items_observed": len(candidate_ids), "click_through_rate": round(len(clicks) / len(trajectory), 4) if trajectory else 0.0},
            "preference_change": {key: round(float(final.get(key, 0)) - float(initial.get(key, 0)), 4) for key in ("curiosity", "satisfaction", "boredom")},
            "final_psychological_state": final,
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agent_count": len(agents),
        "total_interactions": sum(agent["interaction_count"] for agent in agents),
        "total_clicks": sum(agent["click_count"] for agent in agents),
        "agents": agents,
    }


class MultiAgentManager:
    """CAMEL/OASIS-style manager: schedules independent agent-environment loops."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.profiles = load_profiles(resolve_path(args.profiles_path), args.agent_num)
        self.run_dir = resolve_path(args.output_dir) / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.browser_gate = asyncio.Semaphore(args.max_concurrency)
        self.llm_gate = threading.BoundedSemaphore(args.llm_concurrency)
        self.logger = logging.getLogger(f"multi_agent.{self.run_dir.name}")
        self.logger.setLevel(logging.INFO)
        handler = logging.FileHandler(self.run_dir / "manager.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.handlers.clear(); self.logger.addHandler(handler)
        self._handler = handler

    async def _run_one(self, profile: UserProfile) -> dict[str, Any]:
        agent_dir = self.run_dir / profile.user_id
        agent_dir.mkdir(parents=True, exist_ok=False)
        profile_path = agent_dir / "profile.json"
        profile_path.write_text(json.dumps(asdict(profile), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        agent_args = build_agent_args(self.args, profile_path, agent_dir)
        async with self.browser_gate:
            self.logger.info("starting %s", profile.user_id)
            try:
                result = await asyncio.to_thread(run_web_agent, agent_args, None, agent_dir, self.llm_gate)
                self.logger.info("completed %s", profile.user_id)
                return {"agent_id": profile.user_id, "status": "completed", "run_dir": str(result)}
            except Exception as exc:
                self.logger.exception("agent %s failed", profile.user_id)
                return {"agent_id": profile.user_id, "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    async def run(self) -> Path:
        try:
            results = await asyncio.gather(*(self._run_one(profile) for profile in self.profiles))
            summary = analyze_run(self.run_dir)
            summary["manager"] = {"policy": self.args.policy, "max_concurrency": self.args.max_concurrency, "llm_concurrency": self.args.llm_concurrency, "websim_url": self.args.websim_url, "agent_results": results}
            (self.run_dir / "global_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            lines = ["WebSim Multi-Agent Summary", "==========================", f"Agents requested: {self.args.agent_num}", f"Agents completed: {sum(row['status'] == 'completed' for row in results)}", f"Total interactions: {summary['total_interactions']}", f"Total clicks: {summary['total_clicks']}", ""]
            (self.run_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
            return self.run_dir
        finally:
            self._handler.close(); self.logger.removeHandler(self._handler)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run multiple independent LLM user Agents concurrently on one WebSim service.")
    p.add_argument("--agent-num", type=int, default=3)
    p.add_argument("--profiles-path", default="data/multi_agent/profiles.json")
    p.add_argument("--policy", choices=["llm", "rule"], default="llm")
    p.add_argument("--track", type=int, default=5)
    p.add_argument("--websim-url", default="http://127.0.0.1:19002/")
    p.add_argument("--output-dir", default="runs/multi_agent")
    p.add_argument("--max-concurrency", type=int, default=3, help="Maximum simultaneous browser Agent sessions.")
    p.add_argument("--llm-concurrency", type=int, default=2, help="Maximum simultaneous API requests across all agents.")
    p.add_argument("--model-name", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--request-timeout", type=float, default=30)
    p.add_argument("--max-retries", type=int, default=1)
    p.add_argument("--memory-window", type=int, default=5)
    p.add_argument("--decision-threshold", type=float, default=1.0)
    p.add_argument("--disable-fallback", action="store_true")
    p.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--save-screenshots", action=argparse.BooleanOptionalAction, default=True)
    import os
    p.set_defaults(api_key=os.getenv("MODEL_API_KEY"), model_name=os.getenv("MODEL_NAME", "gpt-4o-mini"), base_url=os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1"))
    return p


def main() -> int:
    args = parser().parse_args()
    if args.max_concurrency < 1 or args.llm_concurrency < 1:
        parser().error("max-concurrency and llm-concurrency must be at least 1")
    try:
        result = asyncio.run(MultiAgentManager(args).run())
    except (OSError, ValueError, RuntimeError) as exc:
        parser().error(str(exc))
    print(f"Completed multi-agent run. Results: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

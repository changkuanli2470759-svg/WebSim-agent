"""Browser-executed LLM user Agent for the repository's WebSim interface.

The module follows CAMEL-inspired separation of role, memory, policy and tools,
and OASIS-inspired environment/observation/action trajectory design.  It does
not require CAMEL at runtime: CAMEL is an architectural reference, while the
browser environment is the existing WebSim web application.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

try:
    from .llm_user_agent import LLMPolicy, PolicyError, fallback
    from .mini_agent import RecommendationItem, RuleBasedPolicy, UserAgent, load_profile, resolve_path
except ImportError:
    from llm_user_agent import LLMPolicy, PolicyError, fallback
    from mini_agent import RecommendationItem, RuleBasedPolicy, UserAgent, load_profile, resolve_path


class WebSimTools(Protocol):
    """Tool contract: the policy observes, but only this executor touches a page."""

    def observe_page(self) -> "WebSimObservation": ...
    def click(self, item_id: str) -> "WebSimObservation": ...
    def next_page(self) -> "WebSimObservation": ...
    def refresh(self) -> "WebSimObservation": ...
    def close(self) -> None: ...


@dataclass
class WebSimObservation:
    candidates: list[RecommendationItem]
    status_text: str
    page_url: str
    next_page_available: bool
    screenshot_path: str | None = None


class PlaywrightWebSimTools:
    """Concrete WebSim tool set that observes DOM cards and performs real clicks."""

    def __init__(self, url: str, *, headless: bool, screenshot_dir: Path | None = None, storage_state_path: Path | None = None) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is required. Run: python -m pip install playwright; python -m playwright install chromium") from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._storage_state_path = storage_state_path
        context_args: dict[str, Any] = {"viewport": {"width": 1440, "height": 1000}}
        if storage_state_path and storage_state_path.exists():
            context_args["storage_state"] = str(storage_state_path)
        self._context = self._browser.new_context(**context_args)
        self._page = self._context.new_page()
        self._screenshot_dir = screenshot_dir
        if screenshot_dir:
            screenshot_dir.mkdir(parents=True, exist_ok=True)
        if storage_state_path and storage_state_path.exists():
            parts = urlsplit(url)
            query = dict(parse_qsl(parts.query)); query["resume"] = "1"
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        self._page.goto(url, wait_until="networkidle")
        self._page.locator(".card-main").first.wait_for(state="visible", timeout=15000)
        self._observation_index = 0

    @staticmethod
    def _categories(description: str) -> list[str]:
        normalized = description.replace("|", ",").replace("/", ",")
        return [part.strip().lower() for part in normalized.split(",") if part.strip()][:5]

    def observe_page(self) -> WebSimObservation:
        cards = self._page.locator(".card").evaluate_all(
            """cards => cards.map(card => ({
                item_id: card.dataset.movieId || '',
                title: card.querySelector('.title')?.textContent?.trim() || '',
                description: card.querySelector('.desc')?.textContent?.trim() || '',
                rating: card.querySelector('.rating-group .metric-value')?.textContent?.trim() || '',
                heat: card.querySelector('.heat-group .metric-value')?.textContent?.trim() || ''
            }))"""
        )
        candidates = [
            RecommendationItem(str(row["item_id"]), str(row["title"]), self._categories(str(row["description"])), str(row["description"]))
            for row in cards if row.get("item_id")
        ]
        if not candidates:
            raise RuntimeError("WebSim page has no visible recommendation cards")
        shot = None
        if self._screenshot_dir:
            shot_path = self._screenshot_dir / f"observation_{self._observation_index:03d}.png"
            self._page.screenshot(path=str(shot_path), full_page=True)
            self._observation_index += 1
            shot = str(shot_path)
        next_button = self._page.locator("#nextBtn")
        return WebSimObservation(
            candidates=candidates,
            status_text=self._page.locator("#statusText").inner_text(),
            page_url=self._page.url,
            next_page_available=next_button.is_enabled(),
            screenshot_path=shot,
        )

    def click(self, item_id: str) -> WebSimObservation:
        if '"' in item_id or "'" in item_id:
            raise ValueError("unsafe WebSim item id")
        target = self._page.locator(f'.card-main[data-movie-id="{item_id}"]')
        if target.count() != 1:
            raise ValueError(f"WebSim item is not uniquely clickable: {item_id}")
        with self._page.expect_response(lambda response: "/api/select" in response.url and response.request.method == "POST", timeout=15000):
            target.click()
        self._page.locator(".card-main").first.wait_for(state="visible", timeout=15000)
        return self.observe_page()

    def next_page(self) -> WebSimObservation:
        target = self._page.locator("#nextBtn")
        if not target.is_enabled():
            raise RuntimeError("WebSim next-page action is unavailable")
        with self._page.expect_response(lambda response: "/api/next" in response.url and response.request.method == "POST", timeout=15000):
            target.click()
        self._page.locator(".card-main").first.wait_for(state="visible", timeout=15000)
        return self.observe_page()

    def refresh(self) -> WebSimObservation:
        self._page.reload(wait_until="networkidle")
        self._page.locator(".card-main").first.wait_for(state="visible", timeout=15000)
        return self.observe_page()

    def close(self) -> None:
        if self._storage_state_path:
            self._storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(self._storage_state_path))
        self._context.close()
        self._browser.close()
        self._playwright.stop()


class ActionExecutor:
    """Validates the parsed policy decision before dispatching to browser tools."""

    def __init__(self, tools: WebSimTools) -> None:
        self.tools = tools

    def execute(self, decision: dict[str, Any], observation: WebSimObservation) -> tuple[str, WebSimObservation]:
        action = decision["action"]
        if action == "click":
            allowed = {item.item_id for item in observation.candidates}
            if decision.get("item_id") not in allowed:
                raise ValueError("action parser rejected an item not present on the page")
            return action, self.tools.click(str(decision["item_id"]))
        if action == "next_page":
            if observation.next_page_available:
                return action, self.tools.next_page()
            # The first random WebSim page has no next page. Refresh is a real page
            # action and preserves the agent's intention to reject this candidate set.
            return "refresh", self.tools.refresh()
        raise ValueError(f"action parser rejected unsupported action: {action}")


def _safe_config(args: argparse.Namespace) -> dict[str, Any]:
    result = {key: value for key, value in vars(args).items() if key != "api_key"}
    result["api_key_configured"] = bool(args.api_key)
    return result


def _log_setup(run_dir: Path) -> tuple[logging.Logger, logging.FileHandler]:
    logger = logging.getLogger(f"websim_agent.{run_dir.name}")
    for old in logger.handlers[:]:
        old.close(); logger.removeHandler(old)
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(run_dir / "agent.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger, handler


def run(args: argparse.Namespace, tools: WebSimTools | None = None, run_dir: Path | None = None, llm_request_gate: Any = None) -> Path:
    if args.track < 1:
        raise ValueError("track must be at least 1")
    run_dir = run_dir or (resolve_path(args.output_dir) / datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    run_dir.mkdir(parents=True, exist_ok=True)
    logger, handler = _log_setup(run_dir)
    created_tools = tools is None
    try:
        profile = load_profile(resolve_path(args.profile_path))
        agent = UserAgent(profile, RuleBasedPolicy())
        policy = LLMPolicy(args.api_key, args.base_url, args.model_name, timeout=args.request_timeout, max_retries=args.max_retries, memory_window=args.memory_window, target_field="target", request_gate=llm_request_gate)
        tools = tools or PlaywrightWebSimTools(args.websim_url, headless=args.headless, screenshot_dir=run_dir / "screenshots" if args.save_screenshots else None)
        executor = ActionExecutor(tools)
        observation = tools.observe_page()
        for step in range(1, args.track + 1):
            before = dict(agent.state)
            error = None
            prompt_summary = f"WebSim page with {len(observation.candidates)} cards; {min(len(agent.memory), args.memory_window)} recent memory steps"
            if args.policy == "llm":
                try:
                    decision, _ = policy.decide(asdict(profile), observation.candidates, agent.memory, agent.state)
                    policy_used = "llm"
                except PolicyError as exc:
                    if args.disable_fallback:
                        raise
                    error = str(exc)
                    logger.warning("LLM decision failed; using rule fallback: %s", error)
                    decision, policy_used = fallback(agent, observation.candidates, args.decision_threshold), "fallback_rule"
            else:
                decision, policy_used = fallback(agent, observation.candidates, args.decision_threshold), "rule"
            # Profile identity is local authority; do not trust a model-supplied ID.
            decision["agent_id"] = profile.user_id
            executed_action, result = executor.execute(decision, observation)
            transition = agent.update_state(decision, observation.candidates)
            agent.memory.append({
                "step": step,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "profile_id": profile.user_id,
                "observation": {"status_text": observation.status_text, "page_url": observation.page_url, "screenshot": observation.screenshot_path, "candidates": [asdict(item) for item in observation.candidates]},
                "available_actions": ["click", "refresh"] + (["next_page"] if observation.next_page_available else []),
                "psychological_state_before": before,
                "prompt_summary": prompt_summary,
                "llm_decision": decision,
                "executed_action": executed_action,
                "result": {"status_text": result.status_text, "page_url": result.page_url, "candidate_ids": [item.item_id for item in result.candidates]},
                "policy_used": policy_used,
                "model_name": args.model_name if policy_used == "llm" else None,
                "api_error": error,
                "psychological_state_after": dict(agent.state),
                "psychological_transition": transition,
                "behavioral_state": agent.behavioral_state(),
            })
            observation = result
        payload = {"profile": asdict(profile), "trajectory": agent.memory, "final_psychological_state": agent.state, "final_behavioral_state": agent.behavioral_state()}
        (run_dir / "memory.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (run_dir / "config.json").write_text(json.dumps(_safe_config(args), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        clicks = [row["llm_decision"]["item_id"] for row in agent.memory if row["executed_action"] == "click"]
        lines = ["WebSim LLM PsyBer-Agent Run", "============================", f"User: {profile.user_id}", f"Steps: {len(agent.memory)}", f"Clicks: {len(clicks)}", f"Next pages: {sum(row['executed_action'] == 'next_page' for row in agent.memory)}", f"Refreshes: {sum(row['executed_action'] == 'refresh' for row in agent.memory)}", "Clicked items: " + (", ".join(clicks) if clicks else "(none)"), "Final state: " + json.dumps(agent.state, sort_keys=True), ""]
        (run_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        logger.info("completed %s real WebSim actions", len(agent.memory))
        return run_dir
    finally:
        if created_tools and tools is not None:
            tools.close()
        handler.close(); logger.removeHandler(handler)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run an LLM user Agent that performs real WebSim browser clicks.")
    p.add_argument("--policy", choices=["llm", "rule"], default="llm")
    p.add_argument("--track", type=int, default=5)
    p.add_argument("--profile-path", default="data/mini_agent/user_profile.json")
    p.add_argument("--websim-url", default="http://127.0.0.1:19002/")
    p.add_argument("--output-dir", default="runs/websim_agent")
    p.add_argument("--model-name", default=os.getenv("MODEL_NAME", "gpt-4o-mini"))
    p.add_argument("--base-url", default=os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1"))
    p.add_argument("--request-timeout", type=float, default=30)
    p.add_argument("--max-retries", type=int, default=1)
    p.add_argument("--memory-window", type=int, default=5)
    p.add_argument("--decision-threshold", type=float, default=1.0, help="Rule-policy click threshold and LLM fallback threshold.")
    p.add_argument("--disable-fallback", action="store_true")
    p.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--save-screenshots", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(api_key=os.getenv("MODEL_API_KEY"))
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        run_dir = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        parser().error(str(exc))
    print(f"Completed {args.track} real WebSim actions. Results: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

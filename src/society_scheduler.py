"""Time-stepped, resource-bounded agent-society scheduler.

This module is deliberately separate from :mod:`multi_agent`: the latter keeps
the original small-scale, persistent-browser experiment intact.  This scheduler
stores inactive users as data, activates a bounded subset for one action per
global timestep, and uses a lightweight recommendation environment for large
population experiments.  It follows the OASIS Environment--Observation--Action
--Memory loop and the CAMEL-style separation of manager, role and policy.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import random
import sqlite3
import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from .addiction_metrics import control_failure_probability, population_summary as addiction_population_summary, score as addiction_score, stop_pressure, update_evidence
    from .problematic_use import active_goal, activity_baseline, begin_session, build_daily_session_schedule, commit_session, default_memory as default_risk_memory, longitudinal_score, population_summary as problematic_population_summary, record_step as record_problematic_step, simulation_clock
    from .llm_user_agent import LLMPolicy, PolicyError, fallback
    from .mini_agent import RecommendationItem, RuleBasedPolicy, UserAgent, UserProfile, clamp, initial_behavioral_state, initial_psychological_state, load_items, resolve_path
    from .multi_agent import load_profiles
    from .websim_agent import ActionExecutor, PlaywrightWebSimTools, WebSimObservation
except ImportError:
    from addiction_metrics import control_failure_probability, population_summary as addiction_population_summary, score as addiction_score, stop_pressure, update_evidence
    from problematic_use import active_goal, activity_baseline, begin_session, build_daily_session_schedule, commit_session, default_memory as default_risk_memory, longitudinal_score, population_summary as problematic_population_summary, record_step as record_problematic_step, simulation_clock
    from llm_user_agent import LLMPolicy, PolicyError, fallback
    from mini_agent import RecommendationItem, RuleBasedPolicy, UserAgent, UserProfile, clamp, initial_behavioral_state, initial_psychological_state, load_items, resolve_path
    from multi_agent import load_profiles
    from websim_agent import ActionExecutor, PlaywrightWebSimTools, WebSimObservation


class AgentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    SLEEPING = "SLEEPING"
    OFFLINE = "OFFLINE"
    FINISHED = "FINISHED"


@dataclass
class SocietyAgentState:
    agent_id: str
    profile: dict[str, Any]
    psychological_state: dict[str, float]
    status: str = AgentStatus.ACTIVE.value
    next_eligible_timestep: int = 1
    action_count: int = 0
    recent_memory: list[dict[str, Any]] | None = None
    behavioral_state: dict[str, Any] = field(default_factory=initial_behavioral_state)
    addiction_state: dict[str, Any] = field(default_factory=dict)
    risk_memory: dict[str, Any] = field(default_factory=default_risk_memory)
    problematic_use_state: dict[str, Any] = field(default_factory=dict)
    session_schedule: list[int] = field(default_factory=list)
    session_schedule_cursor: int = 0
    current_session_action_count: int = 0

    def as_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "SocietyAgentState":
        payload=json.loads(value)
        payload.setdefault("behavioral_state",initial_behavioral_state())
        payload.setdefault("addiction_state",{})
        payload.setdefault("risk_memory",default_risk_memory())
        payload.setdefault("problematic_use_state",{})
        payload.setdefault("session_schedule",[])
        payload.setdefault("session_schedule_cursor",0)
        payload.setdefault("current_session_action_count",0)
        return cls(**payload)


class AgentStateStore:
    """SQLite-backed state store; inactive agents use disk, not threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS agent_state (agent_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.connection.commit()

    def initialize(self, states: list[SocietyAgentState]) -> None:
        self.connection.executemany(
            "INSERT OR REPLACE INTO agent_state(agent_id, payload) VALUES (?, ?)",
            [(state.agent_id, state.as_json()) for state in states],
        )
        self.connection.commit()

    def get(self, agent_id: str) -> SocietyAgentState:
        row = self.connection.execute("SELECT payload FROM agent_state WHERE agent_id = ?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(agent_id)
        return SocietyAgentState.from_json(row[0])

    def save(self, state: SocietyAgentState) -> None:
        self.connection.execute("INSERT OR REPLACE INTO agent_state(agent_id, payload) VALUES (?, ?)", (state.agent_id, state.as_json()))

    def commit(self) -> None:
        self.connection.commit()

    def all(self) -> list[SocietyAgentState]:
        return [SocietyAgentState.from_json(row[0]) for row in self.connection.execute("SELECT payload FROM agent_state ORDER BY agent_id")]

    def close(self) -> None:
        self.connection.close()


class SocialState:
    """Shared, compact social signal exposed to active users."""

    def __init__(self, recent_limit: int = 50) -> None:
        self.clicks: Counter[str] = Counter()
        self.agent_clicks: Counter[str] = Counter()
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=recent_limit)

    def observe(self, profile: dict[str, Any]) -> dict[str, Any]:
        trending = [item_id for item_id, _ in self.clicks.most_common(5)]
        return {
            "trending_items": trending,
            "popular_users": [agent_id for agent_id, _ in self.agent_clicks.most_common(3)],
            "recent_events": list(self.recent_events)[-10:],
            "social_graph": {"model": "implicit_similarity", "profile_interests": profile.get("interests", [])},
        }

    def record(self, agent_id: str, action: str, item_id: str | None, timestep: int) -> None:
        event = {"timestep": timestep, "agent_id": agent_id, "action": action, "item_id": item_id}
        self.recent_events.append(event)
        if action == "click" and item_id:
            self.clicks[item_id] += 1
            self.agent_clicks[agent_id] += 1

    def snapshot(self) -> dict[str, Any]:
        return {"trending_items": [item for item, _ in self.clicks.most_common(20)], "popular_users": [agent for agent, _ in self.agent_clicks.most_common(10)], "recent_events": list(self.recent_events)}


class PacedRequestGate:
    """Global API gate: bounds concurrent requests and their start rate."""
    def __init__(self, concurrency: int, minimum_interval: float) -> None:
        self.semaphore = threading.BoundedSemaphore(concurrency)
        self.minimum_interval = max(0.0, minimum_interval)
        self.lock = threading.Lock()
        self.next_request_at = 0.0

    def __enter__(self) -> "PacedRequestGate":
        self.semaphore.acquire()
        with self.lock:
            delay = self.next_request_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_request_at = time.monotonic() + self.minimum_interval
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.semaphore.release()


class LocalRecommendationEnvironment:
    """Deterministic, stateless-on-compute environment for 1k--10k agent runs."""

    def __init__(self, items: list[RecommendationItem], seed: int, candidate_num: int, social: SocialState, condition: str = "control") -> None:
        self.items, self.seed, self.candidate_num, self.social, self.condition = items, seed, candidate_num, social, condition
        if not 1 <= candidate_num <= len(items):
            raise ValueError("candidate-num must be between 1 and the item count")

    def observe(self, state: SocietyAgentState, timestep: int) -> dict[str, Any]:
        rng = random.Random(f"{self.seed}:{state.agent_id}:{timestep}")
        if self.condition == "control":
            selected = rng.sample(self.items, self.candidate_num)
        else:
            interests = set(state.profile.get("interests", []))
            scored = []
            for item in self.items:
                preference = len(interests & set(item.categories))
                social_boost = self.social.clicks.get(item.item_id, 0) if self.condition == "social" else 0
                scored.append((2.0 * preference + .25 * social_boost + rng.random(), item))
            selected = [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:self.candidate_num]]
        social_information = self.social.observe(state.profile) if self.condition == "social" else {"trending_items": [], "popular_users": [], "recent_events": [], "social_graph": {"model": "hidden"}}
        return {
            "candidates": [asdict(item) for item in selected],
            "social_information": social_information,
            "environment": "simulator", "recommendation_condition": self.condition,
        }


def _clone_profiles(templates: list[UserProfile], agent_num: int, seed: int) -> list[UserProfile]:
    """Reuse authored personas, with deterministic small state variation at scale."""
    result: list[UserProfile] = []
    rng = random.Random(seed)
    for index in range(agent_num):
        base = templates[index % len(templates)]
        if index < len(templates):
            result.append(base)
            continue
        shift = (rng.random() - 0.5) * 0.12
        payload = asdict(base)
        payload["user_id"] = f"agent_{index + 1:05d}"
        for key in ("curiosity", "initial_satisfaction", "initial_boredom", "exploration_tendency"):
            payload[key] = round(min(1.0, max(0.0, float(payload[key]) + shift)), 4)
        payload["self_control"] = round(min(1.0, max(0.0, float(payload.get("self_control",.65)) - shift / 2)),4)
        payload["identity_summary"] = (str(payload.get("identity_summary","")).rstrip(".")+f" Variant {index+1} retains the cohort routine with bounded individual differences.").strip()
        payload["generation_metadata"] = {**dict(payload.get("generation_metadata",{})),"scheduler_clone_seed":seed,"template_user_id":base.user_id}
        result.append(UserProfile.from_dict(payload))
    return result


class SocietyScheduler:
    """Selects a bounded active subset and runs exactly one action per selection."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        profiles_path=resolve_path(args.profiles_path)
        try: available=json.loads(profiles_path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError) as exc: raise ValueError(f"Cannot inspect profiles file: {exc}") from exc
        if not isinstance(available,list) or not available: raise ValueError("Profiles JSON must contain a non-empty list")
        templates = load_profiles(profiles_path,min(len(available),args.agent_num))
        self.profiles = _clone_profiles(templates, args.agent_num, args.seed)
        self.resuming = bool(getattr(args, "resume_run", None))
        self.run_dir = resolve_path(args.resume_run) if self.resuming else resolve_path(args.output_dir) / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if self.resuming:
            if not self.run_dir.is_dir():
                raise ValueError(f"Resume run directory not found: {self.run_dir}")
        else:
            self.run_dir.mkdir(parents=True, exist_ok=False)
        self.store = AgentStateStore(self.run_dir / "agent_states.sqlite3")
        self.events_path = self.run_dir / "memory_events.jsonl"
        self.checkpoint_path = self.run_dir / "checkpoint.json"
        self.social = SocialState()
        self.llm_gate = PacedRequestGate(args.llm_concurrency, getattr(args, "llm_min_interval", 1.0))
        self.worker_gate = asyncio.Semaphore(args.max_concurrency)
        self.llm_cache: dict[str, dict[str, Any]] = {}
        self.llm_calls = 0
        self.cache_hits = 0
        self.rng = random.Random(args.seed)
        self.environment_name = getattr(args, "environment", "simulator")
        self.recommendation_condition = getattr(args, "recommendation_condition", "control")
        self.environment = LocalRecommendationEnvironment(load_items(resolve_path(args.items_path)), args.seed, args.candidate_num, self.social, self.recommendation_condition) if self.environment_name == "simulator" else None
        self.tools_factory = PlaywrightWebSimTools
        self.session_dir = self.run_dir / "websim_sessions"
        if self.environment_name == "websim":
            websim_max_agents = getattr(args, "websim_max_agents", 100)
            if args.agent_num > websim_max_agents:
                raise ValueError(f"websim environment supports at most {websim_max_agents} agents; use simulator mode for larger populations")
            self.session_dir.mkdir(parents=True, exist_ok=True)
        self.start_timestep = 1
        if self.resuming:
            if not self.checkpoint_path.exists():
                raise ValueError("Cannot resume without checkpoint.json")
            checkpoint = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            self.start_timestep = int(checkpoint.get("timestep", 0)) + 1
            if not self.store.all():
                raise ValueError("Cannot resume without AgentState records")
            if self.events_path.exists():
                for line in self.events_path.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    action = event.get("action", {})
                    self.social.record(str(event["agent_id"]), str(action.get("action")), action.get("item_id"), int(event["timestep"]))
        else:
            states=[]
            for profile in self.profiles:
                schedule=self._build_session_schedule(asdict(profile),profile.user_id)
                multi_session=getattr(self.args,"daily_multi_session",False)
                states.append(SocietyAgentState(
                    profile.user_id,asdict(profile),initial_psychological_state(profile),
                    status=AgentStatus.OFFLINE.value if multi_session else AgentStatus.ACTIVE.value,
                    next_eligible_timestep=schedule[0] if multi_session and schedule else 1,
                    recent_memory=[],behavioral_state=initial_behavioral_state(),risk_memory=default_risk_memory(),
                    session_schedule=schedule,session_schedule_cursor=0,current_session_action_count=0,
                ))
            self.store.initialize(states)
            self._write_config()

    def _write_config(self) -> None:
        payload = {key: value for key, value in vars(self.args).items() if key != "api_key"}
        payload["api_key_configured"] = bool(self.args.api_key)
        (self.run_dir / "config.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _build_session_schedule(self,profile:dict[str,Any],agent_id:str)->list[int]:
        if not getattr(self.args,"daily_multi_session",False):
            return [1]
        return build_daily_session_schedule(
            profile,agent_id=agent_id,seed=self.args.seed,max_timesteps=self.args.max_timesteps,
            start_hour=float(getattr(self.args,"start_hour",0.0)),timestep_minutes=float(getattr(self.args,"timestep_minutes",15.0)),
            sessions_min=int(getattr(self.args,"sessions_per_day_min",2)),sessions_max=int(getattr(self.args,"sessions_per_day_max",4)),
            minimum_gap_steps=int(getattr(self.args,"minimum_session_gap_steps",8)),
        )

    def _eligible(self, timestep: int) -> list[SocietyAgentState]:
        self._rollover_due_sessions(timestep)
        states = [state for state in self.store.all() if state.status in {AgentStatus.ACTIVE.value, AgentStatus.IDLE.value, AgentStatus.SLEEPING.value,AgentStatus.OFFLINE.value} and state.next_eligible_timestep <= timestep]
        clock=self._clock(timestep)
        # The authored 24-hour baseline provides activity propensity.  Important
        # goals lower activation according to self-control, instead of making the
        # scheduler choose users solely from transient engagement.
        def priority(state:SocietyAgentState)->float:
            profile=UserProfile.from_dict(state.profile); agent=UserAgent(profile,RuleBasedPolicy()); agent.restore(state.psychological_state,state.behavioral_state)
            s=agent.state; baseline=activity_baseline(state.profile,clock["hour_decimal"])
            goal=active_goal(state.profile,clock["hour_decimal"]); conflict_cost=float(goal.get("priority",0))*profile.self_control if goal else 0.0
            return .34*s["engagement"]+.20*s["curiosity"]+.14*profile.exploration_tendency+.25*baseline-.12*s["fatigue"]-.10*s["boredom"]-.25*conflict_cost
        return sorted(states, key=lambda state: (-priority(state), state.action_count, self.rng.random()))

    def _clock(self,timestep:int)->dict[str,Any]:
        return simulation_clock(timestep,float(getattr(self.args,"start_hour",20.0)),float(getattr(self.args,"timestep_minutes",15.0)))

    def _rollover_due_sessions(self,timestep:int)->None:
        """Make planned entries hard boundaries so a long/sleeping visit cannot consume the next session."""
        if not getattr(self.args,"daily_multi_session",False):
            return
        changed=False
        for state in self.store.all():
            next_planned=state.session_schedule[state.session_schedule_cursor] if state.session_schedule_cursor<len(state.session_schedule) else None
            if state.risk_memory.get("current_session") is not None and next_planned is not None and next_planned<=timestep:
                commit_session(state.risk_memory,timestep=max(1,timestep-1),end_reason="next_scheduled_session")
                state.problematic_use_state=longitudinal_score(state.risk_memory)
                state.status=AgentStatus.OFFLINE.value
                state.next_eligible_timestep=timestep
                state.behavioral_state["last_session_end_timestep"]=max(1,timestep-1)
                self.store.save(state); changed=True
        if changed: self.store.commit()

    def select_active_agents(self, timestep: int) -> list[SocietyAgentState]:
        selected=self._eligible(timestep)[: self.args.active_agents_per_step]
        for state in selected:
            previous=state.status
            state._activation_status=previous
            state._wake_recovery=None
            if previous==AgentStatus.SLEEPING.value:
                state._wake_recovery=self._recover_from_sleep(state,timestep)
            if previous==AgentStatus.OFFLINE.value:
                state._wake_recovery=self._recover_between_sessions(state,timestep)
                planned=state.session_schedule[state.session_schedule_cursor] if state.session_schedule_cursor<len(state.session_schedule) else timestep
                begin_session(state.risk_memory,timestep=timestep,simulation_time=self._clock(timestep)["label"],condition=self.recommendation_condition,entry_type="scheduled",planned_start_timestep=planned)
                state.session_schedule_cursor+=1
            state.status=AgentStatus.ACTIVE.value
        return selected

    def _agent_from_state(self,state:SocietyAgentState)->UserAgent:
        agent=UserAgent(UserProfile.from_dict(state.profile),RuleBasedPolicy(self.args.decision_threshold))
        agent.restore(state.psychological_state,state.behavioral_state)
        state.psychological_state=dict(agent.state); state.behavioral_state=agent.behavioral_state()
        return agent

    def _needs_llm(self, agent: UserAgent, candidates: list[RecommendationItem], social: dict[str, Any]) -> bool:
        if self.args.policy != "llm":
            return False
        best = max((agent.score_item(item) for item in candidates), default=0.0)
        # Clear choices use the cheap policy. Social information warrants an LLM
        # only for personas that are explicitly susceptible to popularity.
        trending = {str(item_id) for item_id in social.get("trending_items", [])}
        personality = str(agent.profile.personality).lower()
        social_score=float(agent.profile.media_behavior.get("popularity_susceptibility",agent.profile.social_context.get("peer_influence",0)))
        socially_susceptible = social_score>=.60 or any(word in personality for word in ("popular", "follower", "social", "trend"))
        social_choice = socially_susceptible and bool(trending & {item.item_id for item in candidates})
        return self.args.llm_uncertainty_low <= best <= self.args.llm_uncertainty_high or social_choice

    def _decision(self, state: SocietyAgentState, candidates: list[RecommendationItem], social: dict[str, Any], context: dict[str, Any] | None = None) -> tuple[dict[str, Any], str, str | None]:
        agent=self._agent_from_state(state)
        if not self._needs_llm(agent, candidates, social):
            decision = fallback(agent, candidates, self.args.decision_threshold)
            return decision, "fast_rule", None
        cache_key = hashlib.sha256(json.dumps({"profile": state.profile, "state": state.psychological_state, "candidates": [item.item_id for item in candidates], "trending": social.get("trending_items", []),"context":context}, sort_keys=True).encode()).hexdigest()
        if cache_key in self.llm_cache:
            self.cache_hits += 1
            return copy.deepcopy(self.llm_cache[cache_key]), "llm_cache", None
        try:
            llm = LLMPolicy(self.args.api_key, self.args.base_url, self.args.model_name, timeout=self.args.request_timeout, max_retries=self.args.max_retries, memory_window=self.args.memory_window, target_field="target", request_gate=self.llm_gate)
            enriched_profile = dict(state.profile)
            enriched_profile["social_information"] = social
            enriched_profile["current_context"] = context or {}
            enriched_profile["longitudinal_memory_summary"] = longitudinal_score(state.risk_memory)
            decision, _ = llm.decide(enriched_profile, candidates, state.recent_memory or [], state.psychological_state)
            self.llm_calls += 1
            self.llm_cache[cache_key] = copy.deepcopy(decision)
            return decision, "llm", None
        except PolicyError as exc:
            if self.args.disable_fallback:
                raise
            return fallback(agent, candidates, self.args.decision_threshold), "fallback_rule", str(exc)

    def _continuation_score(self,state:SocietyAgentState)->float:
        profile=UserProfile.from_dict(state.profile); agent=self._agent_from_state(state); s=agent.state; b=agent.behavior
        habit=min(int(b.get("consecutive_clicks",0)),5)/5
        low_value=min(int(b.get("low_value_streak",0)),4)/4
        skips=min(int(b.get("consecutive_skips",0)),3)/3
        autoplay=float(profile.media_behavior.get("autoplay_susceptibility",.5)); stress_use=float(profile.media_behavior.get("stress_coping_use",.5)); stress=float(profile.lifestyle.get("stress_level",.5))
        protective=clamp(.55*float(profile.lifestyle.get("social_support",.5))+.45*float(profile.lifestyle.get("daily_structure",.5)))
        return round(.25*s["engagement"]+.19*s["curiosity"]+.16*s["satisfaction"]+.10*profile.exploration_tendency+.08*habit+.07*autoplay+.06*stress_use*stress-.21*s["boredom"]-.22*s["fatigue"]-.10*low_value-.07*skips-.07*protective,4)

    def _exit_reason(self,state:SocietyAgentState,decision:dict[str,Any])->tuple[str|None,float]:
        agent=self._agent_from_state(state); s=agent.state; b=agent.behavior; score=self._continuation_score(state)
        if decision.get("action")=="exit": return "agent_requested_exit",score
        # Preserve explicit threshold overrides used by existing experiments.
        if s["boredom"]>=self.args.exit_boredom and s["curiosity"]<=self.args.exit_curiosity: return "bored_and_not_curious",score
        minimum=self.args.min_actions_before_satisfied_exit
        session_actions=state.current_session_action_count if getattr(self.args,"daily_multi_session",False) else state.action_count
        if session_actions<minimum: return None,score
        if getattr(self.args,"daily_multi_session",False) and session_actions>=int(getattr(self.args,"max_actions_per_session",8)): return "scheduled_session_limit",score
        if s["boredom"]>=self.args.exit_boredom and s["satisfaction"]<=getattr(self.args,"exit_low_satisfaction",.25): return "bored_and_dissatisfied",score
        if int(b.get("consecutive_skips",0))>=getattr(self.args,"max_consecutive_skips",3) and (s["satisfaction"]<=.45 or s["boredom"]>=.55): return "repeated_rejection",score
        if int(b.get("low_value_streak",0))>=getattr(self.args,"max_low_value_streak",4) and (s["engagement"]<=.50 or s["fatigue"]>=.65): return "sustained_low_value",score
        if s["fatigue"]>=getattr(self.args,"exit_fatigue",.78) and s["engagement"]<=getattr(self.args,"exit_engagement",.45): return "fatigued_disengagement",score
        if s["satisfaction"]>=self.args.exit_satisfaction and s["curiosity"]<=self.args.exit_curiosity and s["fatigue"]>=.30: return "goal_satisfied",score
        if score<=getattr(self.args,"continuation_threshold",.05): return "low_continuation_drive",score
        return None,score

    def _continue(self, state: SocietyAgentState, decision: dict[str, Any]) -> bool:
        return self._exit_reason(state,decision)[0] is None

    def _recover_from_sleep(self,state:SocietyAgentState,timestep:int)->dict[str,float|int]:
        agent=self._agent_from_state(state); s=agent.state; b=agent.behavior
        started=int(b.get("sleep_started_timestep",max(0,timestep-getattr(self.args,"sleep_steps",2))))
        elapsed=max(1,timestep-started)
        before=dict(s)
        s["fatigue"]=clamp(s["fatigue"]-(.10+.035*min(elapsed,5)))
        s["boredom"]=clamp(s["boredom"]-(.07+.025*min(elapsed,5)))
        s["curiosity"]=clamp(s["curiosity"]+.025*min(elapsed,4))
        s["engagement"]=clamp(s["engagement"]+.03)
        b["wake_count"]=int(b.get("wake_count",0))+1
        state.psychological_state=dict(s); state.behavioral_state=agent.behavioral_state()
        return {"elapsed_timesteps":elapsed,"fatigue_delta":round(s["fatigue"]-before["fatigue"],4),"boredom_delta":round(s["boredom"]-before["boredom"],4),"curiosity_delta":round(s["curiosity"]-before["curiosity"],4)}

    def _recover_between_sessions(self,state:SocietyAgentState,timestep:int)->dict[str,float|int|str]:
        agent=self._agent_from_state(state); s=agent.state; b=agent.behavior; before=dict(s)
        previous_end=int(b.get("last_session_end_timestep",max(0,timestep-1)))
        elapsed=max(1,timestep-previous_end)
        s["fatigue"]=clamp(s["fatigue"]-(.15+.025*min(elapsed,12)))
        s["boredom"]=clamp(s["boredom"]-(.10+.02*min(elapsed,10)))
        s["curiosity"]=clamp(s["curiosity"]+.04+.01*min(elapsed,8))
        s["engagement"]=clamp(.75*s["engagement"]+.25*initial_psychological_state(agent.profile)["engagement"])
        for key in ("consecutive_clicks","consecutive_skips","low_value_streak","high_engagement_streak"):
            b[key]=0
        state.current_session_action_count=0
        state.psychological_state=dict(s); state.behavioral_state=agent.behavioral_state()
        return {"type":"scheduled_session_entry","elapsed_timesteps":elapsed,"fatigue_delta":round(s["fatigue"]-before["fatigue"],4),"boredom_delta":round(s["boredom"]-before["boredom"],4),"curiosity_delta":round(s["curiosity"]-before["curiosity"],4)}

    def _next_scheduled_session(self,state:SocietyAgentState,timestep:int)->int|None:
        for index in range(state.session_schedule_cursor,len(state.session_schedule)):
            planned=int(state.session_schedule[index])
            if planned>timestep:
                return planned
        return None

    def _transition(self, state: SocietyAgentState, timestep: int, observation: dict[str, Any], candidates: list[RecommendationItem], decision: dict[str, Any], policy_used: str, api_error: str | None, *, executed_action: str | None = None, result: dict[str, Any] | None = None) -> dict[str, Any]:
        status_before=getattr(state,"_activation_status",state.status)
        agent=self._agent_from_state(state)
        before=dict(agent.state)
        transition=agent.update_state(decision,candidates)
        state.psychological_state = dict(agent.state)
        state.behavioral_state = agent.behavioral_state()
        state.action_count += 1
        state.current_session_action_count += 1
        intended_exit_reason,continuation_score=self._exit_reason(state,decision)
        pressure=stop_pressure(state.psychological_state,transition,state.behavioral_state)
        if intended_exit_reason is None and state.action_count>=self.args.min_actions_before_satisfied_exit and pressure>=getattr(self.args,"stop_pressure_threshold",.60):
            intended_exit_reason="high_stop_pressure"
        exit_intention=intended_exit_reason is not None
        failure_probability=control_failure_probability(state.profile,state.psychological_state,state.behavioral_state,pressure) if exit_intention else 0.0
        control_failure=False
        if exit_intention and decision.get("action")!="exit" and getattr(self.args,"control_failures",False):
            control_failure=random.Random(f"{self.args.seed}:{state.agent_id}:{timestep}:control").random()<failure_probability
        should_continue=not exit_intention or control_failure
        exit_reason=None if should_continue else intended_exit_reason
        session_budget=getattr(self.args,"session_action_budget",12)
        state.behavioral_state=update_evidence(state.behavioral_state,before=before,after=state.psychological_state,transition=transition,continuation_score=continuation_score,exit_intention=exit_intention,actual_continue=should_continue,action_count=state.action_count,session_budget=session_budget)
        state.addiction_state=addiction_score(state.behavioral_state,action_count=state.action_count,session_budget=session_budget)
        if should_continue:
            should_sleep=(state.psychological_state["boredom"]>=self.args.sleep_boredom or state.psychological_state["fatigue"]>=getattr(self.args,"sleep_fatigue",.62))
            state.status=AgentStatus.SLEEPING.value if should_sleep else AgentStatus.IDLE.value
            if should_sleep:
                duration=self.args.sleep_steps+round(2*state.psychological_state["fatigue"]+state.psychological_state["boredom"])
                state.next_eligible_timestep=timestep+max(1,duration)
                state.behavioral_state["sleep_count"]=int(state.behavioral_state.get("sleep_count",0))+1
                state.behavioral_state["sleep_started_timestep"]=timestep
            else: state.next_eligible_timestep=timestep+1
        else:
            next_session=self._next_scheduled_session(state,timestep) if getattr(self.args,"daily_multi_session",False) else None
            state.status = AgentStatus.OFFLINE.value if next_session is not None else AgentStatus.FINISHED.value
            if next_session is not None:
                state.next_eligible_timestep=next_session
                state.behavioral_state["last_session_end_timestep"]=timestep
            state.behavioral_state["last_exit_reason"]=exit_reason
        clock=self._clock(timestep)
        problematic_evidence=record_problematic_step(
            state.risk_memory,agent_id=state.agent_id,timestep=timestep,simulation_time=clock,
            profile=state.profile,exit_intention=exit_intention,actual_continue=should_continue,
            action=str(decision.get("action")),item_id=decision.get("item_id"),reason=str(decision.get("reason","")),
            recommendation_condition=self.recommendation_condition,
            social_signal_visible=bool(observation.get("social_information",{}).get("trending_items")),
        )
        if state.status in {AgentStatus.OFFLINE.value,AgentStatus.FINISHED.value}:
            commit_session(state.risk_memory,timestep=timestep,end_reason=exit_reason or "session_exit")
        state.problematic_use_state=longitudinal_score(state.risk_memory)
        risk_signals={"session_actions":state.action_count,"click_count":int(state.behavioral_state.get("click_count",0)),"consecutive_clicks":int(state.behavioral_state.get("consecutive_clicks",0)),"high_engagement_streak":int(state.behavioral_state.get("high_engagement_streak",0)),"persistence_under_fatigue_steps":int(state.behavioral_state.get("persistence_under_fatigue_steps",0)),"fatigue":state.psychological_state["fatigue"],"engagement":state.psychological_state["engagement"]}
        event = {
            "agent_id": state.agent_id, "timestep": timestep, "status_before":status_before,"status": state.status,
            "session_id":problematic_evidence["session_id"],"simulation_time":clock,
            "observation": observation, "available_actions": ["click", "next_page", "exit"],
            "action": {**decision, "continue": should_continue}, "executed_action": executed_action or decision["action"],
            "result": result, "psychological_state_before": before,
            "psychological_state_after": dict(state.psychological_state), "continue_flag": should_continue,
            "psychological_transition":transition,"continuation_score":continuation_score,"stop_pressure":pressure,
            "exit_intention":exit_intention,"intended_exit_reason":intended_exit_reason,"control_failure_probability":failure_probability,"control_failure":control_failure,"actual_continue":should_continue,"exit_reason":exit_reason,
            "wake_recovery":getattr(state,"_wake_recovery",None),"next_scheduled_session":self._next_scheduled_session(state,timestep),"session_action_count":state.current_session_action_count,"behavioral_risk_signals":risk_signals,
            "addiction_risk":state.addiction_state,
            "problematic_use_evidence":problematic_evidence,"problematic_use_risk":state.problematic_use_state,
            "policy_used": policy_used, "api_error": api_error,
        }
        history = state.recent_memory or []
        state.recent_memory = (history[-(self.args.memory_window - 1):] if self.args.memory_window > 1 else []) + [event]
        return event

    def _act_websim_sync(self, state: SocietyAgentState, timestep: int) -> dict[str, Any]:
        """One real, invisible WebSim page interaction with persisted browser state."""
        tools = self.tools_factory(
            self.args.websim_url, headless=True,
            screenshot_dir=None,
            storage_state_path=self.session_dir / f"{state.agent_id}.json",
        )
        try:
            current = tools.observe_page()
            social = self.social.observe(state.profile) if self.recommendation_condition=="social" else {"trending_items":[],"popular_users":[],"recent_events":[],"social_graph":{"model":"hidden"}}
            candidates = current.candidates
            clock=self._clock(timestep); context={"simulation_time":clock,"current_goal":active_goal(state.profile,clock["hour_decimal"]),"recommendation_condition":self.recommendation_condition}
            decision, policy_used, api_error = self._decision(state, candidates, social, context)
            executed_action, new_observation = ActionExecutor(tools).execute(decision, current)
            observation = {"environment": "websim", "status_text": current.status_text, "page_url": current.page_url, "screenshot": current.screenshot_path, "candidates": [asdict(item) for item in candidates], "social_information": social,"recommendation_condition":self.recommendation_condition}
            result = {"status_text": new_observation.status_text, "page_url": new_observation.page_url, "candidate_ids": [item.item_id for item in new_observation.candidates]}
            return self._transition(state, timestep, observation, candidates, decision, policy_used, api_error, executed_action=executed_action, result=result)
        finally:
            tools.close()

    async def _act_one(self, state: SocietyAgentState, timestep: int) -> dict[str, Any]:
        async with self.worker_gate:
            if self.environment_name == "websim":
                return await asyncio.to_thread(self._act_websim_sync, state, timestep)
            assert self.environment is not None
            observation = self.environment.observe(state, timestep)
            candidates = [RecommendationItem.from_dict(item) for item in observation["candidates"]]
            clock=self._clock(timestep); context={"simulation_time":clock,"current_goal":active_goal(state.profile,clock["hour_decimal"]),"recommendation_condition":self.recommendation_condition}
            decision, policy_used, api_error = await asyncio.to_thread(self._decision, state, candidates, observation["social_information"], context)
            return self._transition(state, timestep, observation, candidates, decision, policy_used, api_error)

    def _checkpoint(self, timestep: int, metrics: list[dict[str, Any]]) -> None:
        status_counts = Counter(state.status for state in self.store.all())
        payload = {"timestep": timestep, "status_counts": dict(status_counts), "llm_calls": self.llm_calls, "llm_cache_hits": self.cache_hits, "social_state": self.social.snapshot(), "recent_step_metrics": metrics[-20:]}
        self.checkpoint_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _finalize_daily_horizon(self,timestep:int)->None:
        """Close open daily sessions and release all Agents at experiment end."""
        if not getattr(self.args,"daily_multi_session",False):
            return
        for state in self.store.all():
            if state.risk_memory.get("current_session") is not None:
                commit_session(state.risk_memory,timestep=timestep,end_reason="simulation_horizon")
            state.problematic_use_state=longitudinal_score(state.risk_memory)
            state.status=AgentStatus.FINISHED.value
            state.behavioral_state["last_exit_reason"]="simulation_horizon" if not state.behavioral_state.get("last_exit_reason") else state.behavioral_state["last_exit_reason"]
            self.store.save(state)
        self.store.commit()

    async def run(self) -> Path:
        metrics: list[dict[str, Any]] = []
        last_timestep = self.start_timestep - 1
        with self.events_path.open("a" if self.resuming else "w", encoding="utf-8") as event_file:
            for timestep in range(self.start_timestep, self.args.max_timesteps + 1):
                active = self.select_active_agents(timestep)
                if not active:
                    # A sleeping Agent is not eligible *yet*. Time must still
                    # advance to its wake-up timestep; only stop when nobody
                    # remains outside FINISHED.
                    if any(state.status != AgentStatus.FINISHED.value for state in self.store.all()):
                        last_timestep = timestep
                        continue
                    break
                last_timestep = timestep
                started = time.perf_counter()
                events = await asyncio.gather(*(self._act_one(state, timestep) for state in active))
                for state in active:
                    self.store.save(state)
                self.store.commit()
                for event in events:
                    event_file.write(json.dumps(event, ensure_ascii=False) + "\n")
                    action = event["action"]
                    self.social.record(event["agent_id"], action["action"], action.get("item_id"), timestep)
                event_file.flush()
                row = {"timestep": timestep, "active_agents": len(active), "elapsed_seconds": round(time.perf_counter() - started, 4), "llm_calls_total": self.llm_calls, "finished_agents": sum(state.status == AgentStatus.FINISHED.value for state in active)}
                metrics.append(row)
                if timestep % self.args.checkpoint_every == 0:
                    self._checkpoint(timestep, metrics)
        final_timestep = last_timestep
        self._finalize_daily_horizon(final_timestep)
        self._checkpoint(final_timestep, metrics)
        (self.run_dir / "llm_cache.json").write_text(json.dumps(self.llm_cache, ensure_ascii=False) + "\n", encoding="utf-8")
        self._write_summary(metrics, final_timestep)
        self.store.close()
        return self.run_dir

    def _write_summary(self, metrics: list[dict[str, Any]], final_timestep: int) -> None:
        states = self.store.all()
        status_counts = Counter(state.status for state in states)
        total_actions = sum(state.action_count for state in states)
        long_session_threshold=getattr(self.args,"addiction_long_session_actions",20)
        behavioral_research_signals={"long_session_threshold":long_session_threshold,"long_session_agents":sum(state.action_count>=long_session_threshold for state in states),"agents_persisting_under_fatigue":sum(int(state.behavioral_state.get("persistence_under_fatigue_steps",0))>0 for state in states),"max_session_actions":max((state.action_count for state in states),default=0),"mean_session_actions":round(total_actions/max(1,len(states)),3),"exit_reasons":dict(Counter(str(state.behavioral_state.get("last_exit_reason","not_finished")) for state in states))}
        session_budget=getattr(self.args,"session_action_budget",12)
        agent_addiction_reports={state.agent_id:(state.addiction_state or addiction_score(state.behavioral_state,action_count=state.action_count,session_budget=session_budget)) for state in states}
        addiction_summary=addiction_population_summary(agent_addiction_reports)
        addiction_report={"population":addiction_summary,"agents":agent_addiction_reports,"notes":["Risk scores are simulation research indicators, not diagnoses.","Null dimensions are unobserved and excluded rather than scored as zero."]}
        (self.run_dir/"addiction_report.json").write_text(json.dumps(addiction_report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        problematic_reports={}
        for state in states:
            report=longitudinal_score(state.risk_memory)
            report["scheduled_session_timesteps"]=state.session_schedule
            report["scheduled_session_times"]=[self._clock(step)["label"] for step in state.session_schedule]
            report["episodic_sessions"]=state.risk_memory.get("episodic_sessions",[])
            if state.risk_memory.get("current_session") is not None:
                report["current_session"]=state.risk_memory["current_session"]
            problematic_reports[state.agent_id]=report
        problematic_summary=problematic_population_summary(problematic_reports)
        problematic_report={"population":problematic_summary,"agents":problematic_reports,"method_notes":["Activity abnormality alone is classified as high engagement, not problematic use.","High risk requires stop failure and high-priority goal conflict repeated across at least two sessions.","Recommendation amplification requires a paired control/treatment comparison with identical persona, initial memory and seed.","Simulation research indicator only; not a clinical diagnosis."]}
        (self.run_dir/"problematic_use_report.json").write_text(json.dumps(problematic_report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        summary = {"agent_num": len(states), "timesteps_executed": final_timestep, "total_actions": total_actions, "status_counts": dict(status_counts), "llm_calls": self.llm_calls, "llm_cache_hits": self.cache_hits, "memory_bytes": self.events_path.stat().st_size if self.events_path.exists() else 0, "per_timestep": metrics, "social_state": self.social.snapshot(),"behavioral_research_signals":behavioral_research_signals,"addiction_risk_summary":addiction_summary,"problematic_use_summary":problematic_summary}
        (self.run_dir / "global_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        lines = ["Agent Society Scheduler Summary", "===============================", f"Agents: {self.args.agent_num}", f"Timesteps: {len(metrics)}", f"Total actions: {total_actions}", f"LLM calls: {self.llm_calls}; cache hits: {self.cache_hits}", f"Memory bytes: {summary['memory_bytes']}", "Status counts: " + json.dumps(dict(status_counts), ensure_ascii=False),"Behavioral research signals: "+json.dumps(behavioral_research_signals,ensure_ascii=False),"Legacy PsyBer-ARI summary: "+json.dumps(addiction_summary,ensure_ascii=False),"Longitudinal problematic-use summary: "+json.dumps(problematic_summary,ensure_ascii=False), ""]
        (self.run_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run a time-stepped, scalable LLM user-agent society.")
    p.add_argument("--agent-num", type=int, default=100)
    p.add_argument("--active-agents-per-step", type=int, default=20)
    p.add_argument("--max-concurrency", type=int, default=10)
    p.add_argument("--llm-concurrency", type=int, default=2)
    p.add_argument("--max-timesteps", type=int, default=100, help="Global experiment horizon, not an agent track limit.")
    p.add_argument("--simulation-days", type=int, default=None, help="Convenience horizon for daily multi-session mode; overrides max-timesteps.")
    p.add_argument("--profiles-path", default="data/multi_agent/profiles.json")
    p.add_argument("--items-path", default="data/mini_agent/items.json")
    p.add_argument("--candidate-num", type=int, default=4)
    p.add_argument("--policy", choices=["llm", "rule"], default="rule")
    p.add_argument("--environment", choices=["simulator", "websim"], default="simulator", help="Use local scalable simulation or real headless WebSim clicks.")
    p.add_argument("--websim-url", default="http://127.0.0.1:19002/")
    p.add_argument("--websim-max-agents", type=int, default=100, help="Safety cap for persistent real-browser sessions.")
    p.add_argument("--output-dir", default="runs/agent_society")
    p.add_argument("--resume-run", default=None, help="Existing society run directory to resume from its checkpoint.")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--start-hour", type=float, default=20.0, help="Simulated local hour at timestep 1.")
    p.add_argument("--timestep-minutes", type=float, default=15.0, help="Simulated minutes advanced per global timestep.")
    p.add_argument("--daily-multi-session", action=argparse.BooleanOptionalAction, default=False, help="Schedule multiple reproducible sessions per Agent per simulated day from its 24-hour baseline.")
    p.add_argument("--sessions-per-day-min", type=int, default=2)
    p.add_argument("--sessions-per-day-max", type=int, default=4)
    p.add_argument("--minimum-session-gap-steps", type=int, default=8, help="Minimum distance between planned session starts; 8 steps equals 2 hours at 15 minutes/step.")
    p.add_argument("--max-actions-per-session", type=int, default=8, help="Session boundary safeguard; a control failure may still extend it.")
    p.add_argument("--recommendation-condition", choices=["control","personalized","social"], default="control", help="Experimental recommendation condition recorded in every observation.")
    p.add_argument("--decision-threshold", type=float, default=1.0)
    p.add_argument("--memory-window", type=int, default=5)
    p.add_argument("--checkpoint-every", type=int, default=1)
    p.add_argument("--sleep-boredom", type=float, default=.68)
    p.add_argument("--sleep-fatigue", type=float, default=.62)
    p.add_argument("--sleep-steps", type=int, default=2)
    p.add_argument("--exit-boredom", type=float, default=.78)
    p.add_argument("--exit-curiosity", type=float, default=.35)
    p.add_argument("--exit-satisfaction", type=float, default=.80)
    p.add_argument("--exit-low-satisfaction", type=float, default=.25, help="End a repeatedly bored, dissatisfied session even if curiosity rose after skips.")
    p.add_argument("--exit-fatigue", type=float, default=.78)
    p.add_argument("--exit-engagement", type=float, default=.45)
    p.add_argument("--min-actions-before-satisfied-exit", type=int, default=3)
    p.add_argument("--max-consecutive-skips", type=int, default=3)
    p.add_argument("--max-low-value-streak", type=int, default=4)
    p.add_argument("--continuation-threshold", type=float, default=.05)
    p.add_argument("--stop-pressure-threshold", type=float, default=.60)
    p.add_argument("--session-action-budget", type=int, default=12, help="Simulated per-session action budget used only as a displacement proxy.")
    p.add_argument("--control-failures", action=argparse.BooleanOptionalAction, default=True, help="Allow a seeded chance of continuing after an exit intention so loss of control can be observed.")
    p.add_argument("--addiction-long-session-actions", type=int, default=20, help="Research reporting threshold only; it never forces an exit or diagnoses addiction.")
    p.add_argument("--llm-uncertainty-low", type=float, default=.5)
    p.add_argument("--llm-uncertainty-high", type=float, default=1.5)
    p.add_argument("--llm-min-interval", type=float, default=1.0, help="Minimum seconds between LLM request starts across all agents.")
    p.add_argument("--model-name", default=None); p.add_argument("--base-url", default=None)
    p.add_argument("--request-timeout", type=float, default=30); p.add_argument("--max-retries", type=int, default=1)
    p.add_argument("--disable-fallback", action="store_true")
    import os
    p.set_defaults(api_key=os.getenv("MODEL_API_KEY"), model_name=os.getenv("MODEL_NAME", "gpt-4o-mini"), base_url=os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1"))
    return p


def main() -> int:
    args = parser().parse_args()
    if args.timestep_minutes <= 0:
        parser().error("timestep-minutes must be greater than 0")
    if args.simulation_days is not None:
        if args.simulation_days < 1:
            parser().error("simulation-days must be at least 1")
        args.max_timesteps=max(1,round(args.simulation_days*1440/args.timestep_minutes))
    for name in ("agent_num", "active_agents_per_step", "max_concurrency", "llm_concurrency", "max_timesteps", "checkpoint_every", "memory_window", "sleep_steps", "min_actions_before_satisfied_exit", "max_consecutive_skips", "max_low_value_streak", "addiction_long_session_actions", "session_action_budget","sessions_per_day_min","sessions_per_day_max","minimum_session_gap_steps","max_actions_per_session"):
        if getattr(args, name) < 1:
            parser().error(f"{name.replace('_', '-')} must be at least 1")
    for name in ("sleep_boredom","sleep_fatigue","exit_boredom","exit_curiosity","exit_satisfaction","exit_low_satisfaction","exit_fatigue","exit_engagement","stop_pressure_threshold"):
        if not 0 <= getattr(args,name) <= 1:
            parser().error(f"{name.replace('_','-')} must be within [0, 1]")
    if args.active_agents_per_step > args.agent_num:
        parser().error("active-agents-per-step cannot exceed agent-num")
    if args.sessions_per_day_min>args.sessions_per_day_max:
        parser().error("sessions-per-day-min cannot exceed sessions-per-day-max")
    if not 0 <= args.start_hour < 24:
        parser().error("start-hour must be within [0, 24)")
    try:
        result = asyncio.run(SocietyScheduler(args).run())
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser().error(str(exc))
    print(f"Completed time-stepped agent society. Results: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Longitudinal problematic-use measurement for simulated users.

The design separates ordinary high engagement from impaired control.  It uses
TinyTroupe-inspired episodic/semantic memory and explicit proposition checks,
while implementing the five observable criteria specified for this project.
It is a simulation research instrument, never a clinical diagnosis.
"""
from __future__ import annotations

import copy
import random
from datetime import datetime, timezone
from typing import Any

VERSION = "psyber-pur-v2"


def clip(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def rate(numerator: int | float, denominator: int | float) -> float | None:
    return None if denominator <= 0 else clip(float(numerator) / float(denominator))


def default_memory() -> dict[str, Any]:
    return {
        "memory_model": "episodic_semantic",
        "next_session_index": 1,
        "current_session": None,
        "episodic_sessions": [],
        "semantic_summary": {},
        "recent_evidence": [],
    }


def simulation_clock(timestep: int, start_hour: float, timestep_minutes: float) -> dict[str, Any]:
    total_minutes = start_hour * 60 + (timestep - 1) * timestep_minutes
    day = int(total_minutes // (24 * 60)) + 1
    minute_of_day = int(total_minutes % (24 * 60))
    hour = minute_of_day // 60
    minute = minute_of_day % 60
    return {"day": day, "hour": hour, "minute": minute, "hour_decimal": round(hour + minute / 60, 4), "label": f"day_{day} {hour:02d}:{minute:02d}"}


def _within(hour: float, start: float, end: float) -> bool:
    return start <= hour < end if start < end else hour >= start or hour < end


def active_goal(profile: dict[str, Any], hour: float, priority_threshold: float = .70) -> dict[str, Any] | None:
    applicable = [goal for goal in profile.get("goals", []) if float(goal.get("priority", 0)) >= priority_threshold and _within(hour, float(goal["start_hour"]), float(goal["end_hour"]))]
    return copy.deepcopy(max(applicable, key=lambda goal: float(goal["priority"]))) if applicable else None


def activity_baseline(profile: dict[str, Any], hour: float) -> float:
    values = profile.get("hourly_activity_baseline", [])
    return clip(values[int(hour) % 24]) if len(values) == 24 else .5


def build_daily_session_schedule(
    profile: dict[str, Any], *, agent_id: str, seed: int, max_timesteps: int,
    start_hour: float, timestep_minutes: float, sessions_min: int = 2,
    sessions_max: int = 4, minimum_gap_steps: int = 8,
) -> list[int]:
    """Generate reproducible within-day session starts from a personal baseline.

    This is weighted sampling, not an independent random decision at every step:
    high-baseline hours are more likely, a minimum gap prevents overlapping
    sessions, and the seed makes the same persona/configuration reproducible.
    """
    if max_timesteps < 1 or timestep_minutes <= 0:
        return []
    rng=random.Random(f"{seed}:{agent_id}:daily-session-schedule")
    steps_per_day=max(1,round(1440/timestep_minutes))
    schedule=[]
    for cycle_start in range(1,max_timesteps+1,steps_per_day):
        cycle_end=min(max_timesteps,cycle_start+steps_per_day-1)
        # Leave enough room for a planned entry to execute and close before the
        # day/horizon boundary. Short test horizons still retain one slot.
        tail_room=min(max(1,minimum_gap_steps//2),max(0,cycle_end-cycle_start))
        slots=list(range(cycle_start,max(cycle_start,cycle_end-tail_room)+1))
        target=min(len(slots),rng.randint(sessions_min,sessions_max))
        chosen=[]
        while slots and len(chosen)<target:
            weights=[]
            for timestep in slots:
                clock=simulation_clock(timestep,start_hour,timestep_minutes)
                baseline=activity_baseline(profile,clock["hour_decimal"])
                goal=active_goal(profile,clock["hour_decimal"])
                self_control=float(profile.get("self_control",.65))
                goal_penalty=(1-float(goal.get("priority",0))*self_control) if goal else 1.0
                weights.append(max(.001,baseline*goal_penalty))
            selected=rng.choices(slots,weights=weights,k=1)[0]
            chosen.append(selected)
            slots=[slot for slot in slots if abs(slot-selected)>=minimum_gap_steps]
        schedule.extend(chosen)
    return sorted(set(schedule))


def begin_session(memory: dict[str, Any], *, timestep: int, simulation_time: str, condition: str, entry_type: str = "scheduled", planned_start_timestep: int | None = None) -> dict[str, Any]:
    if memory.get("current_session") is not None:
        return memory["current_session"]
    index = int(memory.get("next_session_index", 1))
    memory["next_session_index"] = index + 1
    memory["current_session"] = {
        "session_id": f"session_{index:03d}", "start_timestep": timestep,
        "start_time": simulation_time, "end_timestep": None, "end_reason": None,
        "entry_type":entry_type,"planned_start_timestep":planned_start_timestep,
        "recommendation_condition": condition, "action_count": 0,
        "activity_abnormality_sum": 0.0, "stop_intentions": 0,
        "stop_failures": 0, "goal_opportunities": 0, "goal_conflicts": 0,
        "evidence_ids": [],
    }
    return memory["current_session"]


def evaluate_propositions(*, exit_intention: bool, actual_continue: bool, goal: dict[str, Any] | None) -> dict[str, Any]:
    """Deterministic, auditable counterparts of TinyTroupe propositions."""
    return {
        "stop_plan_followed": {
            "applicable": exit_intention, "holds": (not actual_continue) if exit_intention else None,
            "evidence": "Agent continued after an explicit stop intention." if exit_intention and actual_continue else "No stop-plan violation observed.",
        },
        "high_priority_goal_preserved": {
            "applicable": goal is not None, "holds": (not actual_continue) if goal else None,
            "evidence": f"Continued use conflicted with goal '{goal['name']}'." if goal and actual_continue else "No high-priority goal displacement observed.",
        },
    }


def record_step(
    memory: dict[str, Any], *, agent_id: str, timestep: int, simulation_time: dict[str, Any],
    profile: dict[str, Any], exit_intention: bool, actual_continue: bool,
    action: str, item_id: str | None, reason: str, recommendation_condition: str,
    social_signal_visible: bool,
) -> dict[str, Any]:
    session = begin_session(memory, timestep=timestep, simulation_time=simulation_time["label"], condition=recommendation_condition)
    baseline = activity_baseline(profile, simulation_time["hour_decimal"])
    abnormality = clip(1.0 - baseline)  # observed action (1) minus personal hourly baseline
    goal = active_goal(profile, simulation_time["hour_decimal"])
    propositions = evaluate_propositions(exit_intention=exit_intention, actual_continue=actual_continue, goal=goal)
    evidence_id = f"{agent_id}:{session['session_id']}:{timestep}"
    session["action_count"] += 1
    session["activity_abnormality_sum"] = round(float(session["activity_abnormality_sum"]) + abnormality, 4)
    session["stop_intentions"] += int(exit_intention)
    session["stop_failures"] += int(exit_intention and actual_continue)
    session["goal_opportunities"] += int(goal is not None)
    session["goal_conflicts"] += int(goal is not None and actual_continue)
    session["evidence_ids"].append(evidence_id)
    evidence = {
        "evidence_id": evidence_id, "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id, "session_id": session["session_id"], "timestep": timestep,
        "simulation_time": simulation_time, "activity_baseline": baseline,
        "actual_activity": 1, "activity_abnormality": abnormality,
        "intended_action": "exit" if exit_intention else action,
        "actual_action": action if actual_continue else "exit",
        "current_goal": goal, "goal_conflict": bool(goal and actual_continue),
        "recommendation_condition": recommendation_condition,
        "social_signal_visible": social_signal_visible, "selected_item": item_id,
        "decision_reason": reason, "propositions": propositions,
    }
    recent = memory.setdefault("recent_evidence", [])
    recent.append(evidence)
    del recent[:-30]
    memory["semantic_summary"] = longitudinal_score(memory, include_current=True)
    return evidence


def commit_session(memory: dict[str, Any], *, timestep: int, end_reason: str) -> None:
    session = memory.get("current_session")
    if session is None:
        return
    session["end_timestep"] = timestep
    session["end_reason"] = end_reason
    actions = max(1, int(session["action_count"]))
    session["activity_abnormality"] = clip(float(session["activity_abnormality_sum"]) / actions)
    session["stop_failure_rate"] = rate(session["stop_failures"], session["stop_intentions"])
    session["goal_conflict_rate"] = rate(session["goal_conflicts"], session["goal_opportunities"])
    memory.setdefault("episodic_sessions", []).append(copy.deepcopy(session))
    memory["current_session"] = None
    memory["semantic_summary"] = longitudinal_score(memory)


def _sessions(memory: dict[str, Any], include_current: bool) -> list[dict[str, Any]]:
    sessions = copy.deepcopy(memory.get("episodic_sessions", []))
    current = memory.get("current_session")
    if include_current and current:
        current = copy.deepcopy(current)
        actions = max(1, int(current.get("action_count", 0)))
        current["activity_abnormality"] = clip(float(current.get("activity_abnormality_sum", 0)) / actions)
        sessions.append(current)
    return sessions


def longitudinal_score(memory: dict[str, Any], include_current: bool = True) -> dict[str, Any]:
    sessions = _sessions(memory, include_current)
    total_sessions = len(sessions)
    actions = sum(int(s.get("action_count", 0)) for s in sessions)
    stop_intentions = sum(int(s.get("stop_intentions", 0)) for s in sessions)
    stop_failures = sum(int(s.get("stop_failures", 0)) for s in sessions)
    goal_opportunities = sum(int(s.get("goal_opportunities", 0)) for s in sessions)
    goal_conflicts = sum(int(s.get("goal_conflicts", 0)) for s in sessions)
    abnormality = clip(sum(float(s.get("activity_abnormality_sum", 0)) for s in sessions) / max(1, actions)) if actions else None
    combined = sum(int(s.get("stop_failures", 0)) > 0 and int(s.get("goal_conflicts", 0)) > 0 for s in sessions)
    stop_rate = rate(stop_failures, stop_intentions)
    conflict_rate = rate(goal_conflicts, goal_opportunities)
    persistence = rate(combined, total_sessions)
    if actions == 0:
        label = "insufficient_evidence"
    elif total_sessions >= 2 and combined >= 2 and (stop_rate or 0) >= .5 and (conflict_rate or 0) >= .5:
        label = "problematic_use_high_risk"
    elif stop_failures > 0 and goal_conflicts > 0:
        label = "elevated_risk_single_session"
    elif stop_failures > 0 or goal_conflicts > 0:
        label = "watch_state"
    elif abnormality is not None and abnormality >= .5:
        label = "high_engagement"
    else:
        label = "low_risk"
    return {
        "method": VERSION, "risk_label": label, "observed_sessions": total_sessions,
        "activity_abnormality": abnormality, "stop_failure_rate": stop_rate,
        "goal_conflict_rate": conflict_rate, "cross_session_persistence": persistence,
        "counts": {"actions": actions, "stop_intentions": stop_intentions, "stop_failures": stop_failures, "goal_opportunities": goal_opportunities, "goal_conflicts": goal_conflicts, "sessions_with_stop_failure_and_goal_conflict": combined},
        "logic": "High activity alone is engagement; high risk requires stop failure plus goal conflict repeated across sessions.",
        "disclaimer": "Simulation research indicator only; not a clinical diagnosis.",
    }


def population_summary(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    labels: dict[str, int] = {}
    for report in reports.values():
        label = str(report.get("risk_label", "unknown")); labels[label] = labels.get(label, 0) + 1
    return {"method": VERSION, "agent_count": len(reports), "risk_labels": labels,
            "mean_stop_failure_rate": _observed_mean(reports, "stop_failure_rate"),
            "mean_goal_conflict_rate": _observed_mean(reports, "goal_conflict_rate"),
            "mean_cross_session_persistence": _observed_mean(reports, "cross_session_persistence")}


def _observed_mean(reports: dict[str, dict[str, Any]], key: str) -> float | None:
    values = [float(report[key]) for report in reports.values() if report.get(key) is not None]
    return round(sum(values) / len(values), 4) if values else None


def paired_recommendation_effect(control: dict[str, dict[str, Any]], treatment: dict[str, dict[str, Any]], threshold: float = .15) -> dict[str, Any]:
    """Compare the same Agent IDs under control and recommendation treatment."""
    shared = sorted(set(control) & set(treatment))
    effects = []
    pairs = []
    for agent_id in shared:
        c = control[agent_id].get("stop_failure_rate")
        t = treatment[agent_id].get("stop_failure_rate")
        if c is None or t is None:
            continue
        effect = round(float(t) - float(c), 4)
        effects.append(effect); pairs.append({"agent_id": agent_id, "control": c, "treatment": t, "effect": effect})
    mean = round(sum(effects) / len(effects), 4) if effects else None
    return {"formula": "treatment stop-failure rate - control stop-failure rate", "paired_agents": len(effects), "mean_effect": mean, "recommendation_amplified_risk": mean is not None and mean >= threshold, "threshold": threshold, "pairs": pairs}

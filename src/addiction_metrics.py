"""Transparent problematic-use risk metrics for simulated recommendation users.

This is a research instrument, not a clinical diagnostic tool.  It maps
observable simulation behavior to dimensions inspired by ICD-11, SMD/BSMAS and
I-PACE.  Unobservable dimensions remain ``None`` instead of being treated as
evidence of absence.
"""
from __future__ import annotations

from typing import Any

VERSION = "psyber-ari-v1"
DIMENSION_WEIGHTS = {
    "loss_of_control": .25,
    "continued_despite_harm": .25,
    "priority_conflict": .15,
    "tolerance": .10,
    "withdrawal": .10,
    "relapse": .10,
    "mood_modification": .05,
}


def clip(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return None if denominator <= 0 else clip(float(numerator) / float(denominator))


def _mean(parts: list[tuple[float | None, float]]) -> float | None:
    observed = [(value, weight) for value, weight in parts if value is not None]
    if not observed:
        return None
    total = sum(weight for _, weight in observed)
    return clip(sum(float(value) * weight for value, weight in observed) / total)


def stop_pressure(psychological_state: dict[str, float], transition: dict[str, Any], behavioral_state: dict[str, Any]) -> float:
    """Pressure to stop from fatigue, boredom, low satisfaction/value and skips."""
    fatigue = float(psychological_state.get("fatigue", 0.0))
    boredom = float(psychological_state.get("boredom", 0.0))
    satisfaction = float(psychological_state.get("satisfaction", 0.5))
    value = float(transition.get("perceived_value", behavioral_state.get("last_value", 0.5)))
    skips = min(int(behavioral_state.get("consecutive_skips", 0)), 3) / 3
    return clip(.30 * fatigue + .25 * boredom + .20 * (1 - satisfaction) + .15 * (1 - value) + .10 * skips)


def control_failure_probability(profile: dict[str, Any], psychological_state: dict[str, float], behavioral_state: dict[str, Any], pressure: float) -> float:
    """Probability of continuing after a stop intention in the simulator."""
    engagement = float(psychological_state.get("engagement", 0.5))
    exploration = float(profile.get("exploration_tendency", .5))
    self_control = float(profile.get("self_control", .65))
    habit = min(int(behavioral_state.get("consecutive_clicks", 0)), 5) / 5
    media=profile.get("media_behavior",{})
    autoplay=float(media.get("autoplay_susceptibility",.5)); stress_use=float(media.get("stress_coping_use",.5))
    lifestyle=profile.get("lifestyle",{}); stress=float(lifestyle.get("stress_level",.5)); support=float(lifestyle.get("social_support",.5))
    return clip(.03 + .27 * engagement + .22 * habit + .12 * exploration + .10 * autoplay + .08 * stress_use * stress - .28 * self_control - .18 * pressure - .08 * support)


def update_evidence(
    behavioral_state: dict[str, Any], *, before: dict[str, float], after: dict[str, float],
    transition: dict[str, Any], continuation_score: float, exit_intention: bool,
    actual_continue: bool, action_count: int, session_budget: int,
) -> dict[str, Any]:
    """Update compact counters from one action and return the same mapping."""
    evidence = dict(behavioral_state.get("addiction_evidence", {}))
    defaults = {
        "exit_intention_opportunities": 0, "failed_exit_count": 0,
        "low_drive_opportunities": 0, "low_drive_continuations": 0,
        "fatigue_opportunities": 0, "fatigue_continuations": 0,
        "low_value_opportunities": 0, "low_value_continuations": 0,
        "boredom_opportunities": 0, "boredom_continuations": 0,
        "over_budget_opportunities": 0, "over_budget_continuations": 0,
        "negative_mood_opportunities": 0, "mood_relief_count": 0,
    }
    for key, value in defaults.items():
        evidence.setdefault(key, value)
    if exit_intention:
        evidence["exit_intention_opportunities"] += 1
        if actual_continue:
            evidence["failed_exit_count"] += 1
    if continuation_score <= .10:
        evidence["low_drive_opportunities"] += 1
        if actual_continue:
            evidence["low_drive_continuations"] += 1
    if float(after.get("fatigue", 0)) >= .65:
        evidence["fatigue_opportunities"] += 1
        if actual_continue:
            evidence["fatigue_continuations"] += 1
    if int(behavioral_state.get("low_value_streak", 0)) >= 3:
        evidence["low_value_opportunities"] += 1
        if actual_continue:
            evidence["low_value_continuations"] += 1
    if float(after.get("boredom", 0)) >= .68:
        evidence["boredom_opportunities"] += 1
        if actual_continue:
            evidence["boredom_continuations"] += 1
    if action_count > session_budget:
        evidence["over_budget_opportunities"] += 1
        if actual_continue:
            evidence["over_budget_continuations"] += 1
    negative_mood = float(before.get("boredom", 0)) >= .60 or float(before.get("satisfaction", .5)) <= .30
    if negative_mood:
        evidence["negative_mood_opportunities"] += 1
        if float(after.get("engagement", 0)) > float(before.get("engagement", 0)) and float(transition.get("perceived_value", 0)) >= .45:
            evidence["mood_relief_count"] += 1
    behavioral_state["addiction_evidence"] = evidence
    return behavioral_state


def score(behavioral_state: dict[str, Any], *, action_count: int, session_budget: int) -> dict[str, Any]:
    evidence = dict(behavioral_state.get("addiction_evidence", {}))
    failed_exit = _rate(evidence.get("failed_exit_count", 0), evidence.get("exit_intention_opportunities", 0))
    over_budget = _rate(evidence.get("over_budget_continuations", 0), evidence.get("over_budget_opportunities", 0))
    low_drive = _rate(evidence.get("low_drive_continuations", 0), evidence.get("low_drive_opportunities", 0))
    loss_control = _mean([(failed_exit, .40), (over_budget, .35), (low_drive, .25)])
    fatigue = _rate(evidence.get("fatigue_continuations", 0), evidence.get("fatigue_opportunities", 0))
    low_value = _rate(evidence.get("low_value_continuations", 0), evidence.get("low_value_opportunities", 0))
    boredom = _rate(evidence.get("boredom_continuations", 0), evidence.get("boredom_opportunities", 0))
    continued_harm = _mean([(fatigue, .45), (low_value, .35), (boredom, .20)])
    # A simulated action budget is only a displacement proxy, not real-world impairment.
    priority_conflict = clip(max(0, action_count - session_budget) / max(1, session_budget))
    tolerance = None
    if action_count >= max(6, session_budget // 2):
        length_escalation = clip(action_count / max(1, session_budget * 1.5))
        sustained_engagement = clip(int(behavioral_state.get("high_engagement_streak", 0)) / 6)
        declining_value = clip(1 - float(behavioral_state.get("value_ema", .5)))
        tolerance = clip(.50 * length_escalation + .25 * sustained_engagement + .25 * declining_value)
    mood_modification = _rate(evidence.get("mood_relief_count", 0), evidence.get("negative_mood_opportunities", 0))
    dimensions = {
        "loss_of_control": loss_control,
        "continued_despite_harm": continued_harm,
        "priority_conflict": priority_conflict,
        "tolerance": tolerance,
        "withdrawal": None,
        "relapse": None,
        "mood_modification": mood_modification,
    }
    observed_weight = sum(DIMENSION_WEIGHTS[name] for name, value in dimensions.items() if value is not None)
    weighted = sum(DIMENSION_WEIGHTS[name] * float(value) for name, value in dimensions.items() if value is not None)
    risk_score = round(100 * weighted / observed_weight, 2) if observed_weight else None
    core = [dimensions[name] for name in ("loss_of_control", "continued_despite_harm", "priority_conflict")]
    elevated_core = sum(value is not None and value >= .5 for value in core)
    if observed_weight < .60 or risk_score is None:
        label = "insufficient_evidence"
    elif action_count >= session_budget and (loss_control or 0) < .30 and (continued_harm or 0) < .30:
        label = "high_engagement"
    elif risk_score >= 70 and elevated_core >= 2:
        label = "persistent_high_risk"
    elif risk_score >= 50 and elevated_core >= 2:
        label = "high_risk_behavior"
    elif risk_score >= 25:
        label = "elevated_risk"
    else:
        label = "low_risk"
    return {
        "method": VERSION,
        "risk_score": risk_score,
        "risk_label": label,
        "evidence_coverage": round(observed_weight, 2),
        "dimensions": dimensions,
        "observed_evidence": evidence,
        "session_action_budget": session_budget,
        "disclaimer": "Simulation research indicator only; not a clinical diagnosis.",
    }


def population_summary(agent_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reports = list(agent_reports.values())
    scores = [float(report["risk_score"]) for report in reports if report.get("risk_score") is not None]
    labels: dict[str, int] = {}
    for report in reports:
        label = str(report.get("risk_label", "unknown")); labels[label] = labels.get(label, 0) + 1
    ranked = sorted(((agent_id, report.get("risk_score")) for agent_id, report in agent_reports.items() if report.get("risk_score") is not None), key=lambda pair: float(pair[1]), reverse=True)
    return {
        "method": VERSION,
        "agent_count": len(reports),
        "mean_risk_score": round(sum(scores) / len(scores), 2) if scores else None,
        "max_risk_score": round(max(scores), 2) if scores else None,
        "risk_labels": labels,
        "top_risk_agents": [{"agent_id": agent_id, "risk_score": score} for agent_id, score in ranked[:10]],
        "important_limitations": [
            "priority_conflict is currently a simulated time-budget proxy",
            "withdrawal and relapse are unobserved and excluded from the score",
            "thresholds require calibration against validated human labels",
        ],
    }

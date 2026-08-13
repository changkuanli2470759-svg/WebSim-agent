# Longitudinal Problematic-Use Measurement

The project now emits two compatible research reports. `addiction_report.json`
retains the original PsyBer-ARI v1 weighted dimensions. The new
`problematic_use_report.json` implements the longitudinal method in the supplied
design document. Neither report is a clinical diagnosis.

## Design adapted from TinyTroupe

TinyTroupe is used as an architectural reference, not copied or required as a
dependency:

- an authored persona contains interests, self-control, a 24-hour use baseline,
  and high-priority goals;
- each uninterrupted visit is an episodic session;
- completed session summaries form compact semantic/longitudinal memory;
- explicit propositions check whether a stop plan was followed and whether an
  important goal was preserved;
- every proposition points back to an evidence ID in `memory_events.jsonl`.

A temporary `SLEEPING` transition commits the current episode. Waking begins a
short recovery within the same visit. In daily multi-session mode, a completed
visit is committed and the Agent becomes `OFFLINE`; its next baseline-weighted
planned entry begins a new session while preserving prior session summaries.
`FINISHED` commits the last episode. This makes cross-session repetition
measurable without keeping a browser or worker alive for inactive users.

## Five measurements

For simulation hour `h`, the profile stores personal activity baseline `b(h)`.
On an observed active opportunity, activity abnormality is:

```text
A = actual_activity - b(h) = 1 - b(h)
```

This is contextual evidence only. A large `A` with no impaired control is
reported as `high_engagement`, never as high risk.

Stop failure is:

```text
F_stop = continued actions after an exit intention / exit intentions
```

Goal conflict is based on authored daily goals rather than the old action-budget
proxy:

```text
F_goal = continuations during a high-priority goal / high-priority goal opportunities
```

The default profile defines sleep (23:00--07:00, priority 0.95) and work/study
periods (priority 0.85). Profiles may override both the 24 hourly probabilities
and the goal list.

Cross-session persistence is:

```text
P_cross = sessions containing both stop failure and goal conflict / observed sessions
```

Recommendation amplification requires matched control and treatment runs:

```text
E_rec = treatment stop-failure rate - control stop-failure rate
```

The paired runs must use the same Agent IDs/personas, initial memory and seed;
only `--recommendation-condition` should differ.

## Decision labels

- `high_engagement`: activity is unusually high but no stop failure is observed;
- `watch_state`: one core warning (stop failure or goal displacement) is
  observed, but the complete longitudinal pattern is absent;
- `elevated_risk_single_session`: stop failure and goal conflict occur, but have
  not yet repeated across sessions;
- `problematic_use_high_risk`: both occur in at least two sessions, with stop
  failure and goal-conflict rates at least 0.50;
- `insufficient_evidence` or `low_risk`: insufficient or low-risk evidence.

This rule deliberately implements the principle: high clicking is not addiction.
The high-risk label needs impaired control, meaningful conflict and longitudinal
repetition.

## Trace and outputs

Each `memory_events.jsonl` event includes:

```text
session_id, simulation_time
activity_baseline, actual_activity, activity_abnormality
intended_action, actual_action
current_goal, goal_conflict
recommendation_condition, social_signal_visible
selected_item, decision_reason
propositions, problematic_use_risk
```

The compact Agent state in `agent_states.sqlite3` stores recent evidence,
episodic session summaries and the semantic aggregate. The final population and
per-Agent metrics are in `problematic_use_report.json`.

## Running matched recommendation experiments

```powershell
python run_agent_society.py --policy rule --seed 42 --recommendation-condition control
python run_agent_society.py --policy rule --seed 42 --recommendation-condition personalized
python compare_risk_runs.py <control-run-dir> <personalized-run-dir>
```

`social` is a third condition that exposes trending/social signals. Control uses
random local candidates and hides social signals; personalized candidates are
ranked toward profile interests; social adds a popularity boost.

## Limits

The activity abnormality is currently event-opportunity based, not inferred from
human device logs. Default schedules are synthetic and should be authored for
each study population. Thresholds and control-failure coefficients require
calibration against longitudinal human labels. A paired simulation estimates a
model effect under controlled assumptions, not a real-world causal effect.

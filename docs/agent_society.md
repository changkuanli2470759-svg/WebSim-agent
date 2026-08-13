# Time-Stepped Agent Society

`run_agent_society.py` is the scalable counterpart to the existing `run_multi_agent.py` browser experiment. It does **not** create one browser, thread or long-running loop for every person. Agent profiles and dynamic state are stored in SQLite, and the scheduler activates a bounded subset for one action at a time.

## Scheduling model

```text
timestep 1: select <= active-agents-per-step eligible agents -> one action -> checkpoint
timestep 2: select the next eligible agents -> one action -> checkpoint
```

Inactive, sleeping and finished agents are serialized in `agent_states.sqlite3`. Only active agents enter the asyncio worker pool. `max-concurrency` bounds action workers; `llm-concurrency` independently bounds model requests. There is no per-agent `track` parameter. Agent lifecycle is driven by current value, engagement, fatigue and recent action streaks. `max-timesteps` is only a global experimental horizon.

## Psychological dynamics and autonomous exit

The model retains `curiosity`, `satisfaction`, and `boredom`, and adds `engagement` and `fatigue`. All five values are clipped to `[0, 1]`. Each transition derives four explainable environment signals: preference fit, novelty, repeated exposure, and perceived value. Satisfaction moves toward perceived value rather than increasing after every click. A first skip can briefly increase search curiosity, while repeated skips reduce curiosity, engagement and satisfaction and increase boredom/fatigue.

The implementation is **OASIS-inspired**, not an OASIS psychological formula: OASIS supplies the dynamic Environment--Observation--Action--Memory and time-engine pattern, while these coefficients are explicit project assumptions that can be calibrated against real behavioral data.

The scheduler computes a continuation drive:

```text
0.28 engagement + 0.22 curiosity + 0.18 satisfaction
+ 0.12 exploration tendency + 0.10 click-habit signal
- 0.22 boredom - 0.24 fatigue
- 0.12 low-value streak - 0.08 skip streak
```

After the minimum interaction count, an Agent can finish because of repeated rejection, sustained low value, fatigued disengagement, a satisfied goal, or a low continuation drive. Sleep is a temporary recovery state caused by high boredom/fatigue; waking reduces fatigue and boredom before the next action. This normally produces short, heterogeneous sessions rather than requiring hundreds of actions.

Every event stores `psychological_transition`, `continuation_score`, `exit_reason`, `wake_recovery`, and `behavioral_risk_signals`. The latter includes session length, click streak, high-engagement streak, and persistence under fatigue. These are research indicators for later problematic-use analysis; they are not a clinical diagnosis and do not themselves force an exit.

PsyBer-ARI v1 still separates `exit_intention` from `actual_continue`. The longitudinal v2 risk faculty additionally stores episodic sessions, individual 24-hour activity baselines, high-priority goals, proposition results and cross-session persistence. A seeded control failure can therefore model continuing despite an intention to stop; disable it with `--no-control-failures` for deterministic lifecycle studies. See `docs/addiction_metrics.md` for formulas, labels and limitations.

## Large-scale simulator mode

The default environment uses existing local movie JSON and shared social state, so rule mode needs neither a browser nor an API key.

```powershell
python run_agent_society.py `
  --agent-num 10000 `
  --active-agents-per-step 100 `
  --max-concurrency 20 `
  --llm-concurrency 2 `
  --policy rule `
  --max-timesteps 100
```

For LLM mode, configure `MODEL_API_KEY`, `MODEL_BASE_URL`, and `MODEL_NAME`, then use `--policy llm`. A fast rule filter handles clear decisions; only borderline/socially influenced decisions request the LLM. Equivalent LLM contexts are cached within the run. API errors fall back to rules unless `--disable-fallback` is set.

## Social observation

The shared environment tracks click-derived `trending_items`, `popular_users` and bounded `recent_events`. Every active Agent receives this social context in its observation, which is passed to the LLM profile context. This is OASIS-style environment-mediated interaction: agents do not access one another's private memory.

## Outputs and recovery point

```text
agent_states.sqlite3  # persistent AgentState records
memory_events.jsonl   # one event per selected Agent/timestep
checkpoint.json       # timestep, statuses and social state
global_summary.json   # throughput, LLM calls, memory size and totals
addiction_report.json       # compatible PsyBer-ARI v1 dimensions
problematic_use_report.json # longitudinal stop/goal/session method
config.json
summary.txt
```

`global_summary.json` additionally reports session-length distribution, exit reasons, long-session counts, and the number of Agents that continued while both fatigue and engagement were high.

The checkpoint/state files are compact; making one JSON directory per inactive agent would defeat large-scale simulation. State is written at every checkpoint and can be resumed:

```powershell
python run_agent_society.py --resume-run runs/agent_society/<timestamp> --max-timesteps 200
```

## WebSim browser mode

The existing small-scale real-browser path remains unchanged:

```powershell
python run_multi_agent.py --agent-num 10 --policy llm --track 5 `
  --websim-url http://127.0.0.1:19002/ --max-concurrency 3 --llm-concurrency 2
```

It uses persistent Playwright contexts and real WebSim clicks, and is intended for 10--100 agents rather than 10,000. The scheduler shares the same profile, policy, state and memory concepts; its default local simulator remains the resource-bounded path for population-scale studies.

The time-stepped Scheduler now also supports background real-page steps for a small population. Each activation opens an invisible (`headless`) browser context, restores that Agent's saved Flask cookie/session, executes exactly one DOM click or next-page action, saves the browser storage state, then releases the browser resource.

```powershell
python run_agent_society.py `
  --environment websim `
  --websim-url http://127.0.0.1:19002/ `
  --agent-num 10 `
  --active-agents-per-step 3 `
  --max-concurrency 3 `
  --policy rule `
  --max-timesteps 20
```

No browser window is shown. Per-Agent cookies are stored in `websim_sessions/` within the run directory, so the next timestep restores the same WebSim recommendation history. `--websim-max-agents` defaults to 100 as a safety cap; use simulator mode for larger populations.

## Time, goals and recommendation experiments

`--start-hour` (default 20:00) and `--timestep-minutes` (default 15) map each global timestep to a simulated clock. The scheduler uses each persona's hourly activity baseline and current high-priority goal when selecting active users. Every event records this context, so an apparent late-night session can be distinguished from the persona's normal routine.

Use `--recommendation-condition control|personalized|social` to create an auditable experimental condition. Run matched control/treatment simulations with identical profiles and seed, then use `python compare_risk_runs.py <control-run> <treatment-run>` to calculate the paired stop-failure difference. This comparison, rather than popularity exposure alone, is the evidence for recommendation-system amplification.

## Daily multi-session mode

The original scheduler could create another episodic session after temporary
sleep, but a finished Agent could not deliberately return later in the day.
Daily mode adds an explicit `OFFLINE` state and generates 2--4 scheduled session
starts per Agent from its authored 24-hour activity baseline:

```powershell
python run_agent_society.py `
  --agent-num 100 `
  --environment simulator `
  --policy rule `
  --daily-multi-session `
  --simulation-days 1 `
  --start-hour 0 `
  --timestep-minutes 15 `
  --sessions-per-day-min 2 `
  --sessions-per-day-max 4
```

This schedule is weighted random rather than manually fixed: high-baseline
hours are more likely, active high-priority goals reduce the weight according to
self-control, and `--minimum-session-gap-steps` prevents overlapping sessions.
The schedule is deterministic for the same profile, seed and configuration.

When a session ends, the Agent becomes `OFFLINE` if another start remains. At
that start it recovers some fatigue/boredom, begins a new episodic session and
continues with the same long-term profile and memory. `--max-actions-per-session`
is a boundary safeguard, not a risk diagnosis; seeded control failure may still
extend a session. At the daily horizon, any open session is committed with
`simulation_horizon` and all Agents become `FINISHED`.

`problematic_use_report.json` now includes each Agent's planned timestamps and
completed episodic-session summaries, making within-day timing and repeated
stop/goal conflicts directly inspectable.

For richer TinyTroupe-style cohort/person sampling, generate profiles with
`python generate_personas.py` and pass the output through `--profiles-path`.
See `docs/persona_generation.md` for the schema, cohort targeting and research
limits.

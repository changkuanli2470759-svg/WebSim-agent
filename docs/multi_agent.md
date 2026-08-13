# WebSim Multi-Agent User Society

`run_multi_agent.py` coordinates independent user Agents against one shared WebSim service. It applies CAMEL-inspired separation of profile, memory, policy and tools, plus OASIS-style Environment → Observation → Action → Memory loops.

## Concurrency model

Every agent has a distinct browser context, WebSim session cookie, profile, psychological state, screenshots, memory and decision history. They share only the WebSim server URL and the global API request limiter. `asyncio` schedules browser Agent tasks concurrently via `asyncio.to_thread`; `--max-concurrency` limits active browser sessions and `--llm-concurrency` limits API requests through a shared semaphore. This avoids serial runs and reduces rate-limit risk.

Sample distinct profiles are in `data/multi_agent/profiles.json`. They include technology explorers, entertainment-focused viewers, popular-content followers and other behavior styles. Each profile includes interests, dislikes, personality and exploration tendency.

## Run ten agents

Start WebSim first, then from the repository root:

```powershell
python run_multi_agent.py `
  --agent-num 10 `
  --policy llm `
  --track 5 `
  --websim-url http://127.0.0.1:19002/ `
  --max-concurrency 3 `
  --llm-concurrency 2
```

Use the same `MODEL_API_KEY`, `MODEL_BASE_URL`, and `MODEL_NAME` environment variables as the single-user Agent. To validate real browser parallelism with no API calls:

```powershell
python run_multi_agent.py --agent-num 10 --policy rule --track 3 --decision-threshold 0.1 --max-concurrency 3
```

## Outputs

Every run creates `runs/multi_agent/<timestamp>/`. Each `agent_###/` directory contains its own `profile.json`, `memory.json`, `summary.txt`, `config.json`, `agent.log`, and screenshots. The parent directory contains `global_summary.json`, `summary.txt`, and `manager.log`.

`global_summary.json` reports each agent's clicked sequence, session length, interaction count, psychological preference change, observed-candidate count and click-through rate.

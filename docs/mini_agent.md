# Mini PsyBer-Agent

This offline prototype loads a profile and movie catalog, presents candidates, chooses `click` or `next_page`, updates curiosity/satisfaction/boredom, and saves its trajectory.

## Architecture and relationship

`UserProfile` and `RecommendationItem` define inputs. `UserAgent` owns state and memory. `DecisionPolicy` separates decisions; `RuleBasedPolicy` is deterministic. `RecommendationEnvironment` runs the loop. It preserves PsyBer-Agent's profile → observation → decision → action → memory flow while simplifying away WebSim, Playwright, LLM/API pools, learned policies, OT/UGW alignment, and concurrency. Existing advanced modules remain unchanged.

## Install and run

The Agent needs only Python 3.11+ and the standard library.

```bash
python src/mini_agent.py --help
python src/mini_agent.py --track 5
python src/mini_agent.py --track 10 --seed 42
python -m pytest tests/test_mini_agent.py
```

Defaults are in `settings/mini_agent.yaml`; CLI options override them. Relative paths resolve from the repository root.

## Data and outputs

The profile has an ID, interests/dislikes, and initial state values in `[0, 1]`. Items have ID, title, categories, and description. Samples are under `data/mini_agent/`. Each run creates `memory.json`, `summary.txt`, and `config.json` under `runs/mini_agent/<timestamp>/`. Memory includes profile, config, all candidates, reasoned decisions, state before/after each action, and final state.

## Limitations and extensions

Rules are illustrative, category matching is exact after lowercasing, sampling is synthetic, and only one agent runs per process. Future `LLMPolicy`, WebSim environment adapters, or learned PsyBer scoring can use the same interfaces.

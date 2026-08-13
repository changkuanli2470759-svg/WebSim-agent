# LLM-driven PsyBer User Agent

This extends the repository's minimal Agent rather than replacing WebSim, recommenders, training, or fast-policy artifacts. It reuses `mini_agent.py` for profiles, items, scoring fallback, psychological updates, JSON loading, and repository-relative paths. `agent_prompts.py` owns the prompt; `llm_user_agent.py` owns API decisions, validation, fallback, simulation, and outputs.

## Decision flow

The prompt contains the user profile, curiosity/satisfaction/boredom, a configurable recent-memory window, current candidates, and allowed actions. The model must return JSON with `action`, `item_id`, `reason`, and `confidence`. The Agent strips Markdown fences when necessary and validates action, candidate membership, reason, and confidence. Empty/malformed output, timeout, HTTP errors including 401/429/500/502, and missing configuration are logged and fall back to the rule policy. API keys are never persisted.

## API configuration

```powershell
$env:MODEL_API_KEY="your-key"
$env:MODEL_BASE_URL="https://your-compatible-service.example/v1"
$env:MODEL_NAME="your-model"
python src/llm_user_agent.py --policy llm --track 5 --dry-run
```

`MODEL_API_KEY`, `MODEL_BASE_URL`, and `MODEL_NAME` are read from the environment. `--base-url` and `--model-name` override environment defaults. The endpoint must implement OpenAI-compatible `POST /chat/completions`. JSON mode is requested; fenced/plain JSON is also handled.

## Offline and rule modes

```powershell
python src/llm_user_agent.py --policy rule --track 5 --dry-run
python src/llm_user_agent.py --policy llm --track 5 --dry-run
python -m unittest discover -s tests -v
```

The second command safely falls back on every step when no API key is configured. Local JSON candidates make both commands independent of WebSim. Each timestamped `runs/llm_agent/` directory contains `memory.json`, `summary.txt`, `config.json`, and `agent.log`.

## WebSim and limitations

This repository copy does not contain the referenced upstream `src/WebSim_agent.py`, prelearning/alignment Agent files, or API-pool configuration, so direct Playwright control and pool allocation cannot be reused here. The existing Flask WebSim and training/recommender modules are preserved. A later adapter can feed WebSim cards into the same policy and execute its validated action. The current prototype uses one Agent, exact category matching for fallback, and a local candidate sampler.

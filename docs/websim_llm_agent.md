# WebSim LLM User Agent

`src/websim_agent.py` turns the LLM user simulation into real WebSim browser interactions. It keeps the existing Flask WebSim application, recommendation models, and local LLM prototype intact.

## Design

The implementation follows CAMEL's role/memory/policy/tools separation and OASIS's agent–environment–observation–action loop, without requiring a CAMEL runtime package:

```text
Role/Profile + psychological state + memory
  -> LLMPolicy / RuleBasedPolicy
  -> validated Decision JSON
  -> ActionExecutor
  -> PlaywrightWebSimTools
  -> real WebSim card click, Next Page, or page refresh
  -> new observation and persistent trajectory
```

`PlaywrightWebSimTools.observe_page()` reads the visible WebSim DOM cards, status text, Next Page availability, and optionally saves a screenshot. Only `ActionExecutor` invokes browser tools; an LLM never receives browser-control access. A `click` calls the page's existing `/api/select` flow through a real card click. `next_page` clicks the existing Next Page button. If the random initial page has no next page, an LLM rejection safely becomes a real refresh action.

For WebSim, the prompted LLM response uses `target` as the selected page item id:

```json
{"action":"click","target":"393","reason":"Matches the user's interests.","confidence":0.82}
```

The shared validator also accepts the earlier `item_id` spelling and normalizes both forms before the executor sees them.

## Run

Start WebSim first, using a repository copy that has a configured dataset, then run this project from another terminal. On this machine, `C:\Users\lchk\Desktop\surf\WebSim_ready_to_run` is the ready-to-run WebSim copy:

```powershell
cd C:\Users\lchk\Desktop\surf\WebSim_ready_to_run
$env:PORT="19002"
& "C:\Users\lchk\Desktop\surf\CompulsionBench-main\compulsionbench\.venv\Scripts\python.exe" app.py
```

Then, in the LLM Agent project terminal:

```powershell
$env:MODEL_API_KEY="your-key"
$env:MODEL_BASE_URL="https://your-service.example/v1"
$env:MODEL_NAME="your-model"

python src/websim_agent.py --policy llm --track 5 --websim-url http://127.0.0.1:19002/
```

For a free browser validation with real WebSim clicks, use the rule policy:

```powershell
python src/websim_agent.py --policy rule --track 5 --websim-url http://127.0.0.1:19002/
```

Use `--no-headless` to watch the browser, `--no-save-screenshots` to skip observation screenshots, and `--disable-fallback` to expose LLM/API failures instead of falling back to rules.
For a deterministic browser smoke test that deliberately favors click execution, add `--decision-threshold 0.1`.

Each run writes `memory.json`, `summary.txt`, `config.json`, `agent.log`, and (by default) observation screenshots to `runs/websim_agent/<timestamp>/`. The memory records page observation, available actions, parsed policy decision, executed browser action, result, psychological state before/after, and any API error. API keys are never persisted.

## Limits

The current WebSim card UI exposes title, description, rating, and heat; its DOM does not carry a separate genre field, so the controller derives rule-policy categories from the visible description. The browser executor runs one user session at a time. CAMEL and OASIS are design references here because the current project has neither package installed nor the upstream PsyBer `WebSim_agent.py` source.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

python run_agent_society.py `
  --profiles-path data/multi_agent/generated_profiles_100.json `
  --agent-num 10 `
  --active-agents-per-step 3 `
  --max-concurrency 3 `
  --policy rule `
  --environment websim `
  --websim-url http://127.0.0.1:19002/ `
  --daily-multi-session `
  --simulation-days 1 `
  --start-hour 0 `
  --timestep-minutes 30 `
  --sessions-per-day-min 2 `
  --sessions-per-day-max 2 `
  --seed 42

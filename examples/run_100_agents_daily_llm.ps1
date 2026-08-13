$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

foreach ($Name in @("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME")) {
  if (-not (Test-Path "Env:$Name")) { throw "Please configure $Name before running this example." }
}

python run_agent_society.py `
  --profiles-path data/multi_agent/generated_profiles_100.json `
  --agent-num 100 `
  --active-agents-per-step 20 `
  --max-concurrency 10 `
  --llm-concurrency 2 `
  --llm-min-interval 4 `
  --policy llm `
  --environment simulator `
  --daily-multi-session `
  --simulation-days 1 `
  --start-hour 0 `
  --timestep-minutes 15 `
  --sessions-per-day-min 2 `
  --sessions-per-day-max 4 `
  --seed 42 `
  --recommendation-condition personalized

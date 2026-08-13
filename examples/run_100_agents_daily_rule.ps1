$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

& "$PSScriptRoot\01_generate_personas.ps1"

python run_agent_society.py `
  --profiles-path data/multi_agent/generated_profiles_100.json `
  --agent-num 100 `
  --active-agents-per-step 20 `
  --max-concurrency 10 `
  --policy rule `
  --environment simulator `
  --daily-multi-session `
  --simulation-days 1 `
  --start-hour 0 `
  --timestep-minutes 15 `
  --sessions-per-day-min 2 `
  --sessions-per-day-max 4 `
  --minimum-session-gap-steps 8 `
  --max-actions-per-session 8 `
  --seed 42 `
  --recommendation-condition personalized

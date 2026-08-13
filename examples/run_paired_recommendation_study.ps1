$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$OutputRoot = "runs/paired_recommendation_example"

python run_agent_society.py --profiles-path data/multi_agent/generated_profiles_100.json --agent-num 100 --active-agents-per-step 20 --max-concurrency 10 --policy rule --environment simulator --daily-multi-session --simulation-days 1 --start-hour 0 --timestep-minutes 15 --seed 42 --recommendation-condition control --output-dir "$OutputRoot/control"
python run_agent_society.py --profiles-path data/multi_agent/generated_profiles_100.json --agent-num 100 --active-agents-per-step 20 --max-concurrency 10 --policy rule --environment simulator --daily-multi-session --simulation-days 1 --start-hour 0 --timestep-minutes 15 --seed 42 --recommendation-condition personalized --output-dir "$OutputRoot/personalized"

$ControlRun = Get-ChildItem "$OutputRoot/control" -Directory | Sort-Object Name -Descending | Select-Object -First 1
$TreatmentRun = Get-ChildItem "$OutputRoot/personalized" -Directory | Sort-Object Name -Descending | Select-Object -First 1
python compare_risk_runs.py $ControlRun.FullName $TreatmentRun.FullName

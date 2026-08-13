$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

python generate_personas.py `
  --count 100 `
  --seed 42 `
  --population-spec data/multi_agent/population_spec.example.json `
  --output data/multi_agent/generated_profiles_100.json

# Cohort-Directed Persona Generation

The project uses a TinyTroupe-inspired two-stage method: first define a sampling
space and population proportions, then instantiate coherent individuals with
bounded within-group variation. The default generator is local, deterministic
and requires no API key.

## Persona schema

Each generated Agent contains:

- cohort and identity summary;
- age group, gender, residence and household context;
- occupation and schedule type;
- Big Five personality values;
- interests and disliked recommendation categories;
- curiosity, self-control and exploration tendency;
- 24-hour activity baseline and high-priority goals;
- sleep window, stress, social support and daily structure;
- novelty seeking, popularity/autoplay susceptibility, repetition sensitivity
  and stress-coping media use;
- long-term goals and protective factors;
- generation method and seed.

Demographic attributes provide context but never directly create a risk label.
Risk is still based on observed stop failure, goal conflict and cross-session
repetition.

## Included cohorts

- `student`: class/study schedule and relatively high novelty seeking;
- `office_worker`: daytime work constraints and evening leisure;
- `shift_worker`: night work and daytime sleep, demonstrating why one universal
  activity baseline would be biased;
- `retired`: flexible daytime routine and relatively stable preferences.

## Generate profiles

Default representative mix:

```powershell
python generate_personas.py --count 100 --seed 42
```

One target group:

```powershell
python generate_personas.py --count 100 --cohort student --seed 42 `
  --output data/multi_agent/students_100.json
```

Custom population composition:

```powershell
python generate_personas.py --count 100 --seed 42 `
  --cohort-mix student=0.4,office_worker=0.4,shift_worker=0.1,retired=0.1 `
  --output data/multi_agent/target_population_100.json
```

Editable population specification:

```powershell
python generate_personas.py --count 100 --seed 42 `
  --population-spec data/multi_agent/population_spec.example.json `
  --output data/multi_agent/target_population_100.json
```

The specification contains `cohort_mix` and optional `cohort_overrides`. It can
change core traits, media parameters and baseline peaks without editing Python.

The generator uses largest-remainder allocation to preserve requested cohort
proportions, then seeded sampling for individual variation. Reusing the same
configuration and seed produces byte-equivalent profiles.

## Run the generated population

```powershell
python run_agent_society.py `
  --profiles-path data/multi_agent/target_population_100.json `
  --agent-num 100 --daily-multi-session --simulation-days 1 `
  --start-hour 0 --timestep-minutes 15 --policy rule
```

Structured media traits affect rule novelty/repetition weighting, social LLM
activation, continuation drive and control-failure probability. Schedules and
goals affect session timing and goal conflict. The full Persona is also supplied
to the LLM policy.

## Research limits

The built-in cohort values are synthetic behavioral assumptions, not national
statistics or clinical norms. For publication, replace templates/proportions
with cited survey data, document the mapping from survey variables to model
parameters, and run sensitivity/fairness analysis. Do not interpret group-level
risk differences as inherent properties of demographic groups.

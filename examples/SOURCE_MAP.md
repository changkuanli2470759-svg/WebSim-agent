# Example command source map

This file is intended for code-review and presentation screenshots.

| What the command demonstrates | Example command | Definition / implementation |
|---|---|---|
| Persona count, cohort and population specification | `01_generate_personas.ps1` | `src/persona_factory.py:91` (`PersonaFactory`), `src/persona_factory.py:177` (`main`) |
| Society flags | `run_100_agents_daily_rule.ps1` | `src/society_scheduler.py:638` (`parser`) |
| Global time loop | `run_100_agents_daily_rule.ps1` | `src/society_scheduler.py:572` (`SocietyScheduler.run`) |
| Daily session schedule | same | `src/problematic_use.py:60` (`build_daily_session_schedule`) |
| Agent selection | same | `src/society_scheduler.py:299` (`_eligible`), `src/society_scheduler.py:332` (`select_active_agents`) |
| Rule decision | same | `src/mini_agent.py:73` (`RuleBasedPolicy`), `src/mini_agent.py:75` (`decide`) |
| LLM request and fallback | `run_100_agents_daily_llm.ps1` | `src/llm_user_agent.py:64` (`LLMPolicy`), `src/society_scheduler.py:367` (`_decision`) |
| Prompt | LLM example | `src/agent_prompts.py:6` (`SYSTEM_PROMPT`), `src/agent_prompts.py:20` (`build_prompt`) |
| JSON/action validation | LLM example | `src/llm_user_agent.py:19` (`validate_decision`) |
| Web interaction | `run_websim_headless.ps1` | `src/websim_agent.py:48` (`PlaywrightWebSimTools`), `src/websim_agent.py:145` (`ActionExecutor`) |
| Risk evidence and labels | all society examples | `src/problematic_use.py:131` (`record_step`), `src/problematic_use.py:195` (`longitudinal_score`) |
| Paired recommendation effect | paired example | `src/problematic_use.py:245` (`paired_recommendation_effect`), `src/compare_risk_runs.py` |

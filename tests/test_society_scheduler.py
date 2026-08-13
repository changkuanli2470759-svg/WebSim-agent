import argparse
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from src.society_scheduler import AgentStatus, PacedRequestGate, SocietyAgentState, SocietyScheduler
from src.mini_agent import RecommendationItem
from src.websim_agent import WebSimObservation


class FakeWebSimTools:
    created = []

    def __init__(self, url, *, headless, screenshot_dir, storage_state_path):
        self.url, self.headless, self.storage_state_path = url, headless, storage_state_path
        self.created.append(self)

    def observe_page(self):
        return WebSimObservation([RecommendationItem("movie_1", "Movie", ["science"], "science")], "ready", self.url, False)

    def click(self, item_id):
        assert item_id == "movie_1"
        return self.observe_page()

    def next_page(self):
        return self.observe_page()

    def refresh(self):
        return self.observe_page()

    def close(self):
        self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_state_path.write_text("{}", encoding="utf-8")


def args_for(root: Path, output: str, agent_num: int, *, active: int = 10, steps: int = 3):
    return argparse.Namespace(
        agent_num=agent_num, active_agents_per_step=active, max_concurrency=4,
        llm_concurrency=1, max_timesteps=steps,
        profiles_path=str(root / "data/multi_agent/profiles.json"),
        items_path=str(root / "data/mini_agent/items.json"), candidate_num=4,
        policy="rule", output_dir=output, seed=7, decision_threshold=.1,
        memory_window=3, checkpoint_every=1, sleep_boredom=.65, sleep_steps=2,
        exit_boredom=.85, exit_curiosity=.25, exit_satisfaction=.85,
        min_actions_before_satisfied_exit=3, llm_uncertainty_low=.5,
        llm_uncertainty_high=1.5, model_name="mock", base_url="http://mock",
        request_timeout=.1, max_retries=0, disable_fallback=False, api_key=None,
        control_failures=False, session_action_budget=12, stop_pressure_threshold=.60,
        daily_multi_session=False, sessions_per_day_min=2, sessions_per_day_max=4,
        minimum_session_gap_steps=8, max_actions_per_session=8,
        start_hour=20.0, timestep_minutes=15.0, recommendation_condition="control",
    )


class SocietySchedulerTests(unittest.TestCase):
    def test_old_checkpoint_state_gets_behavior_defaults(self):
        old=json.dumps({"agent_id":"old","profile":{},"psychological_state":{"curiosity":.5,"satisfaction":.4,"boredom":.2}})
        state=SocietyAgentState.from_json(old)
        self.assertEqual(state.behavioral_state["consecutive_skips"],0)
        self.assertEqual(state.session_schedule,[])

    def test_daily_multi_session_schedule_is_reproducible_and_baseline_driven(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output1, tempfile.TemporaryDirectory() as output2:
            first=args_for(root,output1,1,active=1,steps=96); first.daily_multi_session=True
            second=args_for(root,output2,1,active=1,steps=96); second.daily_multi_session=True
            scheduler1=SocietyScheduler(first); scheduler2=SocietyScheduler(second)
            state1=scheduler1.store.get("agent_001"); state2=scheduler2.store.get("agent_001")
            self.assertEqual(state1.session_schedule,state2.session_schedule)
            self.assertGreaterEqual(len(state1.session_schedule),2)
            self.assertEqual(state1.status,AgentStatus.OFFLINE.value)
            self.assertEqual(state1.next_eligible_timestep,state1.session_schedule[0])
            scheduler1.store.close(); scheduler2.store.close()

    def test_daily_multi_session_agent_returns_after_session_exit(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            args=args_for(root,output,1,active=1,steps=96); args.daily_multi_session=True
            args.sessions_per_day_min=2; args.sessions_per_day_max=2; args.max_actions_per_session=1
            run_dir=asyncio.run(SocietyScheduler(args).run())
            events=[json.loads(line) for line in (run_dir/"memory_events.jsonl").read_text(encoding="utf-8").splitlines()]
            report=json.loads((run_dir/"problematic_use_report.json").read_text(encoding="utf-8"))["agents"]["agent_001"]
            session_ids={event["session_id"] for event in events}
            self.assertEqual(session_ids,{"session_001","session_002"})
            self.assertEqual(report["observed_sessions"],2)
            self.assertEqual(len(report["scheduled_session_times"]),2)
            self.assertEqual(events[-1]["status"],AgentStatus.FINISHED.value)

    def test_planned_session_start_closes_a_still_sleeping_previous_session(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            args=args_for(root,output,1,active=1,steps=12); args.daily_multi_session=True
            scheduler=SocietyScheduler(args); state=scheduler.store.get("agent_001")
            state.session_schedule=[1,5]; state.session_schedule_cursor=1
            state.status=AgentStatus.SLEEPING.value; state.next_eligible_timestep=10
            state.risk_memory["next_session_index"]=2
            state.risk_memory["current_session"]={"session_id":"session_001","start_timestep":1,"start_time":"day_1 20:00","end_timestep":None,"end_reason":None,"recommendation_condition":"control","entry_type":"scheduled","planned_start_timestep":1,"action_count":1,"activity_abnormality_sum":.5,"stop_intentions":0,"stop_failures":0,"goal_opportunities":0,"goal_conflicts":0,"evidence_ids":[]}
            scheduler.store.save(state); scheduler.store.commit()
            try:
                selected=scheduler.select_active_agents(5)
                self.assertEqual(len(selected),1)
                self.assertEqual(selected[0].risk_memory["current_session"]["session_id"],"session_002")
                self.assertEqual(selected[0].risk_memory["episodic_sessions"][0]["end_reason"],"next_scheduled_session")
            finally:
                scheduler.store.close()

    def test_paced_request_gate_spaces_request_starts(self):
        gate=PacedRequestGate(2,.02)
        start=time.monotonic()
        with gate: pass
        with gate: pass
        self.assertGreaterEqual(time.monotonic()-start,.01)
    def test_scheduler_records_one_action_per_selected_agent_and_checkpoint(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            run_dir = asyncio.run(SocietyScheduler(args_for(root, output, 20, active=5, steps=2)).run())
            events = [json.loads(line) for line in (run_dir / "memory_events.jsonl").read_text(encoding="utf-8").splitlines()]
            summary = json.loads((run_dir / "global_summary.json").read_text(encoding="utf-8"))
            checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(len(events), 10)
            self.assertEqual(summary["agent_num"], 20)
            self.assertEqual(summary["total_actions"], 10)
            self.assertEqual(checkpoint["timestep"], 2)
            self.assertTrue(all("continue_flag" in event and "social_information" in event["observation"] for event in events))
            self.assertTrue(all("addiction_risk" in event and "stop_pressure" in event for event in events))
            self.assertTrue((run_dir / "addiction_report.json").exists())
            self.assertTrue(all("problematic_use_evidence" in event and "session_id" in event for event in events))
            self.assertTrue((run_dir / "problematic_use_report.json").exists())

    def test_large_population_uses_state_store_without_per_agent_memory_files(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            run_dir = asyncio.run(SocietyScheduler(args_for(root, output, 1000, active=25, steps=1)).run())
            summary = json.loads((run_dir / "global_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["agent_num"], 1000)
            self.assertEqual(summary["total_actions"], 25)
            self.assertTrue((run_dir / "agent_states.sqlite3").exists())
            self.assertFalse(any(path.is_dir() and path.name.startswith("agent_") for path in run_dir.iterdir()))

    def test_state_lifecycle_can_finish_without_track_limit(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            args = args_for(root, output, 10, active=10, steps=5)
            args.exit_boredom = 0.0
            args.exit_curiosity = 1.0
            run_dir = asyncio.run(SocietyScheduler(args).run())
            summary = json.loads((run_dir / "global_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status_counts"].get(AgentStatus.FINISHED.value), 10)
            self.assertEqual(summary["timesteps_executed"], 1)

    def test_repeatedly_bored_and_dissatisfied_agent_finishes_even_if_curiosity_is_high(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            scheduler = SocietyScheduler(args_for(root, output, 10, active=1, steps=1))
            state = scheduler.store.get("agent_001")
            state.action_count = 10
            state.psychological_state = {"curiosity": .8, "satisfaction": .05, "boredom": .95}
            self.assertFalse(scheduler._continue(state, {"action": "next_page"}))
            scheduler.store.close()

    def test_three_rejections_end_a_disengaged_session_and_record_reason(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            scheduler = SocietyScheduler(args_for(root, output, 10, active=1, steps=3))
            state = scheduler.store.get("agent_001")
            candidates = [RecommendationItem("bad", "Bad", ["romance"], "bad fit")]
            event = None
            for timestep in range(1, 4):
                event = scheduler._transition(state, timestep, {"candidates": []}, candidates, {"action":"next_page","item_id":None,"reason":"no match","confidence":.8}, "rule", None)
            self.assertEqual(state.status, AgentStatus.FINISHED.value)
            self.assertEqual(event["exit_reason"], "repeated_rejection")
            self.assertIn("continuation_score", event)
            self.assertIn("behavioral_risk_signals", event)
            scheduler.store.close()

    def test_sleep_recovery_reduces_fatigue_and_boredom(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            scheduler = SocietyScheduler(args_for(root, output, 10, active=1, steps=5))
            for candidate in scheduler.store.all():
                candidate.status=AgentStatus.FINISHED.value
                scheduler.store.save(candidate)
            state = scheduler.store.get("agent_001")
            state.status = AgentStatus.SLEEPING.value; state.next_eligible_timestep = 5
            state.psychological_state.update({"fatigue":.8,"boredom":.75,"engagement":.7})
            state.behavioral_state["sleep_started_timestep"] = 2
            scheduler.store.save(state); scheduler.store.commit()
            selected = scheduler.select_active_agents(5)[0]
            self.assertLess(selected.psychological_state["fatigue"],.8)
            self.assertLess(selected.psychological_state["boredom"],.75)
            self.assertEqual(selected.behavioral_state["wake_count"],1)
            scheduler.store.close()

    def test_scheduler_advances_time_until_sleeping_agent_wakes(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            scheduler = SocietyScheduler(args_for(root, output, 10, active=1, steps=3))
            for state in scheduler.store.all():
                state.status = AgentStatus.FINISHED.value
                scheduler.store.save(state)
            sleeper = scheduler.store.get("agent_001")
            sleeper.status = AgentStatus.SLEEPING.value
            sleeper.next_eligible_timestep = 3
            scheduler.store.save(sleeper); scheduler.store.commit()
            run_dir = asyncio.run(scheduler.run())
            events = [json.loads(line) for line in (run_dir / "memory_events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["timestep"], 3)

    def test_resume_uses_checkpointed_states(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            first = args_for(root, output, 20, active=5, steps=1)
            run_dir = asyncio.run(SocietyScheduler(first).run())
            resumed = args_for(root, output, 20, active=5, steps=2)
            resumed.resume_run = str(run_dir)
            asyncio.run(SocietyScheduler(resumed).run())
            summary = json.loads((run_dir / "global_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["timesteps_executed"], 2)
            self.assertEqual(summary["total_actions"], 10)

    def test_websim_environment_runs_one_headless_browser_action_per_activation(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            FakeWebSimTools.created = []
            args = args_for(root, output, 2, active=2, steps=2)
            args.environment = "websim"; args.websim_url = "http://websim/"; args.websim_max_agents = 10
            scheduler = SocietyScheduler(args)
            scheduler.tools_factory = FakeWebSimTools
            run_dir = asyncio.run(scheduler.run())
            events = [json.loads(line) for line in (run_dir / "memory_events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(events), 4)
            self.assertTrue(all(event["observation"]["environment"] == "websim" for event in events))
            self.assertTrue(all(tool.headless for tool in FakeWebSimTools.created))
            self.assertEqual(len(list((run_dir / "websim_sessions").glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()

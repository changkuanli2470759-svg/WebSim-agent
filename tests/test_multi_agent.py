import argparse
import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.multi_agent import MultiAgentManager, analyze_run, load_profiles


class MultiAgentTests(unittest.TestCase):
    def test_sample_profiles_are_distinct(self):
        root=Path(__file__).resolve().parents[1]
        profiles=load_profiles(root/"data/multi_agent/profiles.json",10)
        self.assertEqual(len(profiles),10)
        self.assertEqual(len({profile.user_id for profile in profiles}),10)
        self.assertNotEqual(profiles[0].personality,profiles[1].personality)

    def test_async_manager_runs_agents_in_parallel_and_writes_global_summary(self):
        root=Path(__file__).resolve().parents[1]
        active={"count":0,"peak":0}; lock=threading.Lock()
        def fake_web_run(agent_args, tools, run_dir, request_gate):
            with lock:
                active["count"]+=1; active["peak"]=max(active["peak"],active["count"])
            time.sleep(.04)
            profile=json.loads(Path(agent_args.profile_path).read_text(encoding="utf-8"))
            memory={"profile":profile,"trajectory":[{"psychological_state_before":{"curiosity":.5,"satisfaction":.5,"boredom":.2},"observation":{"candidates":[{"item_id":"1"},{"item_id":"2"}]},"llm_decision":{"item_id":"1"},"executed_action":"click"}],"final_psychological_state":{"curiosity":.4,"satisfaction":.6,"boredom":.1}}
            (run_dir/"memory.json").write_text(json.dumps(memory),encoding="utf-8")
            (run_dir/"summary.txt").write_text("ok",encoding="utf-8")
            with lock: active["count"]-=1
            return run_dir
        with tempfile.TemporaryDirectory() as output:
            args=argparse.Namespace(agent_num=3,profiles_path=str(root/"data/multi_agent/profiles.json"),policy="rule",track=1,websim_url="http://websim/",output_dir=output,max_concurrency=2,llm_concurrency=1,model_name="mock",base_url="url",request_timeout=.1,max_retries=0,memory_window=2,decision_threshold=.1,disable_fallback=False,headless=True,save_screenshots=False,api_key=None)
            with patch("src.multi_agent.run_web_agent",side_effect=fake_web_run):
                run_dir=asyncio.run(MultiAgentManager(args).run())
            summary=json.loads((run_dir/"global_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["agent_count"],3); self.assertEqual(summary["total_clicks"],3)
            self.assertGreaterEqual(active["peak"],2)
            self.assertTrue(all((run_dir/f"agent_00{i}"/"memory.json").exists() for i in range(1,4)))

    def test_analyzer_reports_preference_change(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir=Path(directory); agent=run_dir/"agent_001"; agent.mkdir()
            payload={"profile":{"user_id":"agent_001","personality":"tester","interests":[]},"trajectory":[{"psychological_state_before":{"curiosity":.2,"satisfaction":.3,"boredom":.4},"observation":{"candidates":[{"item_id":"1"}]},"llm_decision":{"item_id":"1"},"executed_action":"click"}],"final_psychological_state":{"curiosity":.3,"satisfaction":.5,"boredom":.1}}
            (agent/"memory.json").write_text(json.dumps(payload),encoding="utf-8")
            summary=analyze_run(run_dir)
            self.assertEqual(summary["agents"][0]["preference_change"],{"curiosity":.1,"satisfaction":.2,"boredom":-.3})

if __name__ == "__main__": unittest.main()

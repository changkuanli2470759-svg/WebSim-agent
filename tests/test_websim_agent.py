import argparse
import json
import tempfile
import unittest
from pathlib import Path

from src.mini_agent import RecommendationItem
from src.websim_agent import ActionExecutor, WebSimObservation, run


def observation(*, next_page=False):
    return WebSimObservation(
        candidates=[RecommendationItem("1", "Space", ["science fiction"], "science fiction, action")],
        status_text="ready", page_url="http://websim/", next_page_available=next_page,
    )


class FakeTools:
    def __init__(self): self.events=[]; self.current=observation()
    def observe_page(self): self.events.append("observe"); return self.current
    def click(self, item_id): self.events.append(("click", item_id)); return self.current
    def next_page(self): self.events.append("next_page"); return self.current
    def refresh(self): self.events.append("refresh"); return self.current
    def close(self): self.events.append("close")


class WebSimAgentTests(unittest.TestCase):
    def test_click_is_dispatched_to_browser_tool(self):
        tools=FakeTools(); action,_=ActionExecutor(tools).execute({"action":"click","item_id":"1"},observation())
        self.assertEqual(action,"click"); self.assertEqual(tools.events,[("click","1")])
    def test_unavailable_next_page_becomes_real_refresh(self):
        tools=FakeTools(); action,_=ActionExecutor(tools).execute({"action":"next_page","item_id":None},observation(next_page=False))
        self.assertEqual(action,"refresh"); self.assertEqual(tools.events,["refresh"])
    def test_next_page_is_dispatched_when_page_allows_it(self):
        tools=FakeTools(); action,_=ActionExecutor(tools).execute({"action":"next_page","item_id":None},observation(next_page=True))
        self.assertEqual(action,"next_page"); self.assertEqual(tools.events,["next_page"])
    def test_invalid_click_target_is_rejected(self):
        with self.assertRaises(ValueError): ActionExecutor(FakeTools()).execute({"action":"click","item_id":"missing"},observation())
    def test_rule_run_writes_web_memory(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output:
            args=argparse.Namespace(policy="rule",track=3,profile_path=str(root/"data/mini_agent/user_profile.json"),websim_url="http://websim/",output_dir=output,model_name="mock",base_url="url",request_timeout=.1,max_retries=0,memory_window=2,decision_threshold=1.0,disable_fallback=False,headless=True,save_screenshots=False,api_key=None)
            result=run(args,tools=FakeTools()); memory=json.loads((result/"memory.json").read_text(encoding="utf-8"))
            self.assertEqual(len(memory["trajectory"]),3)
            self.assertTrue(all("observation" in row and "executed_action" in row for row in memory["trajectory"]))
            self.assertTrue(all(all(0<=value<=1 for value in row["psychological_state_after"].values()) for row in memory["trajectory"]))
            self.assertEqual({p.name for p in result.iterdir()},{"memory.json","summary.txt","config.json","agent.log"})

if __name__ == "__main__": unittest.main()

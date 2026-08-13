import json, tempfile, unittest
from pathlib import Path
from src.mini_agent import RecommendationEnvironment, RecommendationItem, RuleBasedPolicy, UserAgent, UserProfile, load_profile, save_results
def profile(): return UserProfile("u",["science fiction"],["romance"],.7,.5,.2)
def item(item_id="1",categories=None): return RecommendationItem(item_id,"Movie "+item_id,categories or ["science fiction"],"Description")
class MiniAgentTests(unittest.TestCase):
    def test_profile_loading(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"p.json"; p.write_text(json.dumps({"user_id":"u","interests":["Action"],"disliked_categories":[],"curiosity":.5,"initial_satisfaction":.4,"initial_boredom":.1}),encoding="utf-8"); self.assertEqual(load_profile(p).interests,["action"])
    def test_item_scoring_and_repeated_penalty(self):
        a=UserAgent(profile(),RuleBasedPolicy()); m=item(); first=a.score_item(m); a.clicked_item_ids.add(m.item_id); self.assertGreater(first,0); self.assertAlmostEqual(a.score_item(m),first-2.5)
    def test_next_page_behavior(self):
        d=UserAgent(profile(),RuleBasedPolicy(1)).decide([item(categories=["romance"])]); self.assertEqual(d["action"],"next_page"); self.assertTrue(d["reason"])
    def test_psychological_state_bounds(self):
        a=UserAgent(profile(),RuleBasedPolicy(99)); candidates=[item()]
        for _ in range(50): a.update_state(a.decide(candidates),candidates)
        self.assertTrue(all(0<=v<=1 for v in a.state.values()))
    def test_low_value_click_can_reduce_satisfaction(self):
        a=UserAgent(profile(),RuleBasedPolicy()); before=a.state["satisfaction"]
        transition=a.update_state({"action":"click","item_id":"1","confidence":.5},[item(categories=["romance"])])
        self.assertLess(a.state["satisfaction"],before)
        self.assertLess(transition["perceived_value"],.45)
    def test_repeated_skips_exhaust_curiosity_instead_of_increasing_forever(self):
        a=UserAgent(profile(),RuleBasedPolicy(99)); candidates=[item()]
        decision={"action":"next_page","item_id":None}
        a.update_state(decision,candidates); after_first=a.state["curiosity"]
        a.update_state(decision,candidates); a.update_state(decision,candidates)
        self.assertLess(a.state["curiosity"],after_first)
        self.assertEqual(a.behavior["consecutive_skips"],3)
    def test_memory_output_creation(self):
        with tempfile.TemporaryDirectory() as d:
            a=UserAgent(profile(),RuleBasedPolicy()); memory=RecommendationEnvironment(a,[item("1"),item("2")],candidate_num=1,seed=1).run(2); run=save_results(Path(d),{"seed":1},a.profile,memory,a.state); self.assertEqual({p.name for p in run.iterdir()},{"memory.json","summary.txt","config.json"}); self.assertEqual(len(json.loads((run/"memory.json").read_text(encoding="utf-8"))["trajectory"]),2)
    def test_deterministic_execution_with_same_seed(self):
        movies=[item(str(i),["science fiction"] if i%2 else ["romance"]) for i in range(1,7)]
        def execute():
            a=UserAgent(profile(),RuleBasedPolicy()); return RecommendationEnvironment(a,movies,candidate_num=3,seed=42).run(5)
        self.assertEqual(execute(),execute())
if __name__=="__main__": unittest.main()

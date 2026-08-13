import argparse, http.client, json, tempfile, threading, time, unittest, urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from src.llm_user_agent import LLMPolicy, PolicyError, parse_json_content, retry_delay, run, validate_decision
from src.mini_agent import RecommendationItem

ITEMS=[RecommendationItem("1","Neon",["science fiction"],"Space"),RecommendationItem("2","Hearts",["romance"],"Love")]
def response(value): return {"choices":[{"message":{"content":json.dumps(value)}}]}
def policy(transport,retries=0): return LLMPolicy("secret-key","https://example.test/v1","mock-model",max_retries=retries,transport=transport)

class LLMPolicyTests(unittest.TestCase):
    def test_click(self):
        p=policy(lambda *a:response({"action":"click","item_id":"1","reason":"match","confidence":.8})); decision,_=p.decide({},ITEMS,[],{}); self.assertEqual(decision["item_id"],"1")
    def test_next_page(self):
        p=policy(lambda *a:response({"action":"next_page","item_id":None,"reason":"no match","confidence":.7})); decision,_=p.decide({},ITEMS,[],{}); self.assertEqual(decision["action"],"next_page")
    def test_markdown_json_is_cleaned(self): self.assertEqual(parse_json_content('```json\n{"action":"next_page"}\n```')["action"],"next_page")
    def test_invalid_json(self):
        with self.assertRaises(PolicyError): policy(lambda *a:{"choices":[{"message":{"content":"bad"}}]}).decide({},ITEMS,[],{})
    def test_invalid_item_id(self):
        with self.assertRaises(PolicyError): validate_decision({"action":"click","item_id":"999","reason":"x","confidence":.5},ITEMS)
    def test_timeout(self):
        def timeout(*a): raise TimeoutError("timed out")
        with self.assertRaises(PolicyError): policy(timeout).decide({},ITEMS,[],{})
    def test_remote_connection_close_is_policy_error(self):
        def disconnected(*a): raise http.client.RemoteDisconnected("Remote end closed connection without response")
        with self.assertRaisesRegex(PolicyError,"RemoteDisconnected"):
            policy(disconnected).decide({},ITEMS,[],{})
    def test_http_429_and_502(self):
        for code in (429,502):
            def failed(*a,c=code): raise urllib.error.HTTPError("url",c,"error",{},None)
            with self.assertRaisesRegex(PolicyError,f"HTTP {code}"): policy(failed).decide({},ITEMS,[],{})
    def test_429_retry_delay_uses_retry_after(self):
        error=urllib.error.HTTPError("url",429,"error",{"Retry-After":"3"},None)
        self.assertEqual(retry_delay(error,0),3.0)
    def test_missing_key(self):
        with self.assertRaisesRegex(PolicyError,"not configured"): LLMPolicy(None,"url","model").decide({},ITEMS,[],{})
    def test_request_gate_limits_parallel_api_calls(self):
        gate=threading.BoundedSemaphore(1); active={"count":0,"peak":0}; lock=threading.Lock()
        def transport(*args):
            with lock:
                active["count"]+=1; active["peak"]=max(active["peak"],active["count"])
            time.sleep(.03)
            with lock: active["count"]-=1
            return response({"action":"click","item_id":"1","reason":"match","confidence":.8})
        guarded=LLMPolicy("key","https://example.test/v1","mock",request_gate=gate,transport=transport)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _:guarded.decide({},ITEMS,[],{}),range(2)))
        self.assertEqual(active["peak"],1); self.assertEqual(len(results),2)
    def test_fallback_output_and_no_key_leak(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            args=argparse.Namespace(policy="llm",track=5,seed=42,profile_path=str(root/"data/mini_agent/user_profile.json"),items_path=str(root/"data/mini_agent/items.json"),candidate_num=4,output_dir=d,model_name="mock",base_url="https://example.test/v1",request_timeout=.1,max_retries=0,memory_window=3,disable_fallback=False,dry_run=True,api_key=None)
            out=run(args); memory=json.loads((out/"memory.json").read_text(encoding="utf-8")); self.assertTrue(all(x["policy_used"]=="fallback_rule" for x in memory["trajectory"])); self.assertTrue(all(all(0<=v<=1 for v in x["psychological_state_after"].values()) for x in memory["trajectory"])); self.assertEqual({p.name for p in out.iterdir()},{"memory.json","summary.txt","config.json","agent.log"})
    def test_rule_seed_determinism(self):
        root=Path(__file__).resolve().parents[1]
        def execute(parent):
            args=argparse.Namespace(policy="rule",track=5,seed=7,profile_path=str(root/"data/mini_agent/user_profile.json"),items_path=str(root/"data/mini_agent/items.json"),candidate_num=4,output_dir=parent,model_name="mock",base_url="url",request_timeout=.1,max_retries=0,memory_window=3,disable_fallback=False,dry_run=True,api_key="SHOULD_NOT_LEAK")
            out=run(args); content="".join(p.read_text(encoding="utf-8") for p in out.iterdir()); self.assertNotIn("SHOULD_NOT_LEAK",content); return json.loads((out/"memory.json").read_text(encoding="utf-8"))["trajectory"]
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            one,two=execute(a),execute(b)
            for rows in (one,two):
                for row in rows: row.pop("timestamp")
            self.assertEqual(one,two)
if __name__=="__main__": unittest.main()

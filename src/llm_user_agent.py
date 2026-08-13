"""LLM-driven PsyBer user Agent with offline rule fallback."""
from __future__ import annotations
import argparse, http.client, json, logging, os, random, re, socket, time, urllib.error, urllib.request
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
try:  # Support both `python src/llm_user_agent.py` and package imports in tests.
    from .agent_prompts import build_prompt
    from .mini_agent import RecommendationItem, RuleBasedPolicy, UserAgent, load_items, load_profile, resolve_path
except ImportError:
    from agent_prompts import build_prompt
    from mini_agent import RecommendationItem, RuleBasedPolicy, UserAgent, load_items, load_profile, resolve_path

Transport=Callable[[str,dict[str,str],dict[str,Any],float],dict[str,Any]]

class PolicyError(ValueError): pass

def validate_decision(value:Any,candidates:list[RecommendationItem])->dict[str,Any]:
    if not isinstance(value,dict): raise PolicyError("response is not a JSON object")
    if "item_id" not in value and "target" in value:
        value = dict(value); value["item_id"] = value["target"]
    missing={"action","item_id","reason"}-value.keys()
    if missing: raise PolicyError("missing fields: "+", ".join(sorted(missing)))
    action=value["action"]
    if action not in {"click","next_page"}: raise PolicyError(f"unsupported action: {action}")
    ids={item.item_id for item in candidates}; item_id=None if value["item_id"] is None else str(value["item_id"])
    if action=="click" and item_id not in ids: raise PolicyError("item_id is not in current candidates")
    if action=="next_page" and item_id is not None: raise PolicyError("next_page item_id must be null")
    reason=value["reason"]
    if not isinstance(reason,str) or not reason.strip(): raise PolicyError("reason must be non-empty")
    confidence=value.get("confidence",.5)
    if isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or not 0<=confidence<=1: raise PolicyError("confidence must be within [0, 1]")
    title=next((x.title for x in candidates if x.item_id==item_id),None)
    result={"action":action,"item_id":item_id,"target":item_id,"title":title,"reason":reason.strip(),"confidence":float(confidence)}
    if "agent_id" in value: result["agent_id"]=str(value["agent_id"])
    return result

def parse_json_content(content:str)->Any:
    if not content or not content.strip(): raise PolicyError("empty model response")
    text=content.strip(); text=re.sub(r"^```(?:json)?\s*|\s*```$","",text,flags=re.I|re.S)
    try: return json.loads(text)
    except json.JSONDecodeError:
        match=re.search(r"\{.*\}",text,re.S)
        if not match: raise PolicyError("invalid JSON response")
        try: return json.loads(match.group())
        except json.JSONDecodeError as exc: raise PolicyError("invalid JSON response") from exc

def urllib_transport(url:str,headers:dict[str,str],payload:dict[str,Any],timeout:float)->dict[str,Any]:
    request=urllib.request.Request(url,data=json.dumps(payload).encode(),headers=headers,method="POST")
    with urllib.request.urlopen(request,timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def retry_delay(error:Exception, attempt:int)->float:
    """Respect provider guidance for 429s before using bounded exponential backoff."""
    if isinstance(error,urllib.error.HTTPError) and error.code==429:
        retry_after=error.headers.get("Retry-After") if error.headers else None
        try:
            if retry_after is not None: return min(max(float(retry_after),0.0),60.0)
        except (TypeError,ValueError): pass
        return min(2.0**attempt,15.0)
    return min(.1*(attempt+1),.5)

class LLMPolicy:
    def __init__(self,api_key:str|None,base_url:str,model_name:str,*,timeout:float=30,max_retries:int=1,memory_window:int=5,target_field:str="item_id",request_gate:Any=None,transport:Transport=urllib_transport)->None:
        self.api_key,self.base_url,self.model_name=api_key,base_url.rstrip("/"),model_name; self.timeout,self.max_retries,self.memory_window,self.target_field,self.request_gate,self.transport=timeout,max_retries,memory_window,target_field,request_gate,transport
    def decide(self,profile:dict[str,Any],candidates:list[RecommendationItem],memory:list[dict[str,Any]],state:dict[str,float])->tuple[dict[str,Any],str]:
        if not self.api_key: raise PolicyError("MODEL_API_KEY is not configured")
        prompt=build_prompt(profile,[asdict(x) for x in candidates],memory,state,self.memory_window,self.target_field)
        payload={"model":self.model_name,"messages":[{"role":"system","content":"Return valid JSON only."},{"role":"user","content":prompt}],"temperature":0.2,"response_format":{"type":"json_object"}}
        error:Exception=PolicyError("unknown API error")
        for attempt in range(self.max_retries+1):
            try:
                if self.request_gate is None:
                    raw=self.transport(self.base_url+"/chat/completions",{"Authorization":"Bearer "+self.api_key,"Content-Type":"application/json"},payload,self.timeout)
                else:
                    with self.request_gate:
                        raw=self.transport(self.base_url+"/chat/completions",{"Authorization":"Bearer "+self.api_key,"Content-Type":"application/json"},payload,self.timeout)
                content=raw["choices"][0]["message"]["content"]
                return validate_decision(parse_json_content(content),candidates),prompt
            except (KeyError,IndexError,TypeError,json.JSONDecodeError,PolicyError,urllib.error.HTTPError,urllib.error.URLError,http.client.HTTPException,ConnectionError,TimeoutError,socket.timeout) as exc:
                error=exc
                if attempt<self.max_retries: time.sleep(retry_delay(exc,attempt))
        status=f"HTTP {error.code}" if isinstance(error,urllib.error.HTTPError) else type(error).__name__
        raise PolicyError(f"{status}: {str(error)[:160]}") from error

def fallback(agent:UserAgent,candidates:list[RecommendationItem],threshold:float=1.0)->dict[str,Any]:
    decision=RuleBasedPolicy(threshold).decide(agent,candidates); decision["confidence"]=.5
    return decision

def run(args:argparse.Namespace)->Path:
    output=resolve_path(args.output_dir)/datetime.now().strftime("%Y%m%d_%H%M%S_%f"); output.mkdir(parents=True)
    logger=logging.getLogger("llm_agent")
    for old_handler in logger.handlers[:]:
        old_handler.close(); logger.removeHandler(old_handler)
    logger.setLevel(logging.INFO); handler=logging.FileHandler(output/"agent.log",encoding="utf-8"); handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s")); logger.addHandler(handler)
    profile=load_profile(resolve_path(args.profile_path)); items=load_items(resolve_path(args.items_path)); agent=UserAgent(profile,RuleBasedPolicy()); rng=random.Random(args.seed)
    llm=LLMPolicy(args.api_key,args.base_url,args.model_name,timeout=args.request_timeout,max_retries=args.max_retries,memory_window=args.memory_window)
    for step in range(1,args.track+1):
        candidates=rng.sample(items,args.candidate_num); before=dict(agent.state); error=None; prompt="rule policy"
        if args.policy=="llm":
            try: decision,prompt=llm.decide(asdict(profile),candidates,agent.memory,agent.state); used="llm"
            except PolicyError as exc:
                error=str(exc); logger.warning("LLM decision failed; using rule fallback: %s",error)
                if args.disable_fallback: raise
                decision=fallback(agent,candidates); used="fallback_rule"
        else: decision=fallback(agent,candidates); used="rule"
        transition=agent.update_state(decision,candidates)
        agent.memory.append({"step":step,"timestamp":datetime.now().isoformat(),"profile_id":profile.user_id,"candidates":[asdict(x) for x in candidates],"psychological_state_before":before,"prompt_summary":f"profile + {min(len(agent.memory),args.memory_window)} recent steps + {len(candidates)} candidates","action":decision["action"],"item_id":decision["item_id"],"reason":decision["reason"],"confidence":decision["confidence"],"policy_used":used,"model_name":args.model_name if used=="llm" else None,"psychological_state_after":dict(agent.state),"psychological_transition":transition,"behavioral_state":agent.behavioral_state(),"api_error":error})
    safe_config={k:v for k,v in vars(args).items() if k!="api_key"}; safe_config["api_key_configured"]=bool(args.api_key)
    (output/"memory.json").write_text(json.dumps({"profile":asdict(profile),"trajectory":agent.memory,"final_psychological_state":agent.state,"final_behavioral_state":agent.behavioral_state()},indent=2,ensure_ascii=False)+"\n",encoding="utf-8"); (output/"config.json").write_text(json.dumps(safe_config,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    clicks=[x["item_id"] for x in agent.memory if x["action"]=="click"]; fallbacks=sum(x["policy_used"]=="fallback_rule" for x in agent.memory)
    (output/"summary.txt").write_text(f"LLM PsyBer-Agent Run\n====================\nUser: {profile.user_id}\nSteps: {args.track}\nClicks: {len(clicks)}\nNext pages: {args.track-len(clicks)}\nFallbacks: {fallbacks}\nClicked items: {', '.join(clicks) if clicks else '(none)'}\nFinal state: {json.dumps(agent.state,sort_keys=True)}\n",encoding="utf-8"); logger.info("completed %s steps",args.track); handler.close(); logger.removeHandler(handler); return output

def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="Run the LLM-driven PsyBer user Agent (local candidate environment).")
    p.add_argument("--policy",choices=["llm","rule"],default="llm"); p.add_argument("--track",type=int,default=5); p.add_argument("--seed",type=int,default=42); p.add_argument("--profile-path",default="data/mini_agent/user_profile.json"); p.add_argument("--items-path",default="data/mini_agent/items.json"); p.add_argument("--candidate-num",type=int,default=4); p.add_argument("--output-dir",default="runs/llm_agent"); p.add_argument("--model-name",default=os.getenv("MODEL_NAME","gpt-4o-mini")); p.add_argument("--base-url",default=os.getenv("MODEL_BASE_URL","https://api.openai.com/v1")); p.add_argument("--request-timeout",type=float,default=30); p.add_argument("--max-retries",type=int,default=1); p.add_argument("--memory-window",type=int,default=5); p.add_argument("--disable-fallback",action="store_true"); p.add_argument("--dry-run",action="store_true",help="Use local JSON candidates (currently the default environment)."); p.set_defaults(api_key=os.getenv("MODEL_API_KEY")); return p
def main()->int:
    args=parser().parse_args()
    try: result=run(args)
    except (OSError,ValueError) as exc: parser().error(str(exc))
    print(f"Completed {args.track} steps. Results: {result}"); return 0
if __name__=="__main__": raise SystemExit(main())

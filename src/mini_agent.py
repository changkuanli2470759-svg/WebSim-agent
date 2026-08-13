"""Offline deterministic psychological user-agent prototype using only stdlib."""
from __future__ import annotations
import argparse, json, random
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS: dict[str, Any] = {"profile_path":"data/mini_agent/user_profile.json","items_path":"data/mini_agent/items.json","track":5,"candidate_num":4,"seed":42,"output_dir":"runs/mini_agent","decision_threshold":1.0}

def clamp(value: float) -> float: return round(max(0.0, min(1.0, value)), 4)

def default_activity_baseline() -> list[float]:
    """Hourly probability of recommendation use for a typical evening user."""
    return [.03,.02,.02,.02,.02,.03,.06,.10,.08,.07,.06,.06,.07,.07,.08,.10,.14,.22,.38,.52,.68,.62,.40,.15]

def default_goals() -> list[dict[str,Any]]:
    """Important routine commitments used to detect real goal displacement."""
    return [
        {"goal_id":"sleep","name":"sleep","category":"health","priority":.95,"start_hour":23.0,"end_hour":7.0},
        {"goal_id":"work_study_am","name":"work or study","category":"productivity","priority":.85,"start_hour":9.0,"end_hour":12.0},
        {"goal_id":"work_study_pm","name":"work or study","category":"productivity","priority":.85,"start_hour":14.0,"end_hour":18.0},
    ]

def initial_psychological_state(profile:"UserProfile")->dict[str,float]:
    """Create bounded affect/engagement state from an authored profile."""
    engagement=clamp(.45*profile.curiosity+.35*profile.initial_satisfaction+.20*(1-profile.initial_boredom))
    fatigue=clamp(.10+.25*profile.initial_boredom)
    return {"curiosity":profile.curiosity,"satisfaction":profile.initial_satisfaction,"boredom":profile.initial_boredom,"engagement":engagement,"fatigue":fatigue}

def initial_behavioral_state()->dict[str,Any]:
    """Compact, checkpoint-friendly session signals used by the scheduler."""
    return {"click_count":0,"next_page_count":0,"consecutive_clicks":0,"consecutive_skips":0,"low_value_streak":0,"high_engagement_streak":0,"persistence_under_fatigue_steps":0,"sleep_count":0,"wake_count":0,"value_ema":0.5,"last_value":0.5,"clicked_item_ids":[],"seen_categories":[],"category_exposures":{}}
def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value): raise ValueError(f"'{field}' must be a list of non-empty strings")
    return [v.strip().lower() for v in value]

@dataclass(frozen=True)
class UserProfile:
    user_id: str; interests: list[str]; disliked_categories: list[str]; curiosity: float; initial_satisfaction: float; initial_boredom: float; personality: str = "balanced"; exploration_tendency: float = 0.5; self_control: float = 0.65; hourly_activity_baseline: list[float] = field(default_factory=default_activity_baseline); goals: list[dict[str,Any]] = field(default_factory=default_goals); cohort: str = "general"; identity_summary: str = ""; demographics: dict[str,Any] = field(default_factory=dict); occupation: dict[str,Any] = field(default_factory=dict); personality_traits: dict[str,float] = field(default_factory=dict); lifestyle: dict[str,Any] = field(default_factory=dict); media_behavior: dict[str,float] = field(default_factory=dict); social_context: dict[str,Any] = field(default_factory=dict); long_term_goals: list[str] = field(default_factory=list); protective_factors: list[str] = field(default_factory=list); generation_metadata: dict[str,Any] = field(default_factory=dict)
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserProfile":
        try:
            baseline=[float(value) for value in data.get("hourly_activity_baseline",default_activity_baseline())]
            goals=[dict(goal) for goal in data.get("goals",default_goals())]
            obj=cls(str(data["user_id"]),_strings(data["interests"],"interests"),_strings(data["disliked_categories"],"disliked_categories"),float(data["curiosity"]),float(data["initial_satisfaction"]),float(data["initial_boredom"]),str(data.get("personality","balanced")),float(data.get("exploration_tendency",data["curiosity"])),float(data.get("self_control",.65)),baseline,goals,str(data.get("cohort","general")),str(data.get("identity_summary","")),dict(data.get("demographics",{})),dict(data.get("occupation",{})),{str(k):float(v) for k,v in data.get("personality_traits",{}).items()},dict(data.get("lifestyle",{})),{str(k):float(v) for k,v in data.get("media_behavior",{}).items()},dict(data.get("social_context",{})),[str(v) for v in data.get("long_term_goals",[])],[str(v) for v in data.get("protective_factors",[])],dict(data.get("generation_metadata",{})))
        except (KeyError,TypeError,ValueError) as exc: raise ValueError(f"Malformed user profile: {exc}") from exc
        for name in ("curiosity","initial_satisfaction","initial_boredom","exploration_tendency","self_control"):
            if not 0 <= getattr(obj,name) <= 1: raise ValueError(f"'{name}' must be within [0, 1]")
        if len(obj.hourly_activity_baseline)!=24 or any(not 0<=value<=1 for value in obj.hourly_activity_baseline): raise ValueError("'hourly_activity_baseline' must contain 24 values within [0, 1]")
        for goal in obj.goals:
            if not {"goal_id","name","priority","start_hour","end_hour"} <= set(goal): raise ValueError("Each goal must contain goal_id, name, priority, start_hour and end_hour")
            if not 0<=float(goal["priority"])<=1 or not 0<=float(goal["start_hour"])<24 or not 0<=float(goal["end_hour"])<24: raise ValueError("Goal priority/hours are outside valid ranges")
        for container_name in ("personality_traits","media_behavior"):
            for key,value in getattr(obj,container_name).items():
                if not 0<=value<=1: raise ValueError(f"'{container_name}.{key}' must be within [0, 1]")
        return obj

@dataclass(frozen=True)
class RecommendationItem:
    item_id: str; title: str; categories: list[str]; description: str
    @classmethod
    def from_dict(cls,data:dict[str,Any])->"RecommendationItem":
        try: return cls(str(data["item_id"]),str(data["title"]),_strings(data["categories"],"categories"),str(data["description"]))
        except (KeyError,TypeError,ValueError) as exc: raise ValueError(f"Malformed recommendation item: {exc}") from exc

class DecisionPolicy(ABC):
    @abstractmethod
    def decide(self,agent:"UserAgent",candidates:Sequence[RecommendationItem])->dict[str,Any]: ...

class RuleBasedPolicy(DecisionPolicy):
    def __init__(self,threshold:float=1.0)->None: self.threshold=threshold
    def decide(self,agent:"UserAgent",candidates:Sequence[RecommendationItem])->dict[str,Any]:
        if not candidates: return {"action":"next_page","item_id":None,"title":None,"score":None,"reason":"No recommendation candidates were available."}
        score,item=max(((agent.score_item(i),i) for i in candidates),key=lambda p:(p[0],p[1].item_id))
        if score < self.threshold: return {"action":"next_page","item_id":None,"title":None,"score":round(score,3),"reason":f"The best candidate scored {score:.2f}, below the {self.threshold:.2f} threshold."}
        matched=sorted(set(item.categories)&set(agent.profile.interests)); unfamiliar=sorted(set(item.categories)-agent.seen_categories); parts=[]
        if matched: parts.append("matches interests: "+", ".join(matched))
        if unfamiliar: parts.append("offers unfamiliar categories: "+", ".join(unfamiliar))
        if item.item_id in agent.clicked_item_ids: parts.append("has a repeated-item penalty")
        return {"action":"click","item_id":item.item_id,"title":item.title,"score":round(score,3),"reason":"The item "+("; ".join(parts) if parts else "has the highest acceptable score")+"."}

class UserAgent:
    def __init__(self,profile:UserProfile,policy:DecisionPolicy)->None:
        self.profile,self.policy=profile,policy; self.state=initial_psychological_state(profile); self.behavior=initial_behavioral_state(); self.memory=[]; self.clicked_item_ids=set(); self.seen_categories=set(); self.category_exposures={}
    def restore(self,state:dict[str,float]|None=None,behavior:dict[str,Any]|None=None)->None:
        if state:
            defaults=initial_psychological_state(self.profile); defaults.update({k:clamp(float(v)) for k,v in state.items() if k in defaults}); self.state=defaults
        if behavior:
            defaults=initial_behavioral_state(); defaults.update(behavior); self.behavior=defaults
            self.clicked_item_ids={str(x) for x in defaults.get("clicked_item_ids",[])}
            self.seen_categories={str(x) for x in defaults.get("seen_categories",[])}
            self.category_exposures={str(k):int(v) for k,v in defaults.get("category_exposures",{}).items()}
    def behavioral_state(self)->dict[str,Any]:
        out=dict(self.behavior); out["clicked_item_ids"]=sorted(self.clicked_item_ids)[-200:]; out["seen_categories"]=sorted(self.seen_categories); out["category_exposures"]=dict(self.category_exposures); return out
    def score_item(self,item:RecommendationItem)->float:
        cats=set(item.categories); interest=2*len(cats&set(self.profile.interests)); dislike=3*len(cats&set(self.profile.disliked_categories)); repeat=2.5 if item.item_id in self.clicked_item_ids else 0; unfamiliar=len(cats-self.seen_categories); exposure=sum(self.category_exposures.get(c,0) for c in cats)
        novelty_weight=float(self.profile.media_behavior.get("novelty_seeking",self.profile.exploration_tendency))
        repeat_sensitivity=float(self.profile.media_behavior.get("repetition_sensitivity",.5))
        return round(interest-dislike-repeat+self.state["curiosity"]*(.35+.45*novelty_weight)*unfamiliar-self.state["boredom"]*(.20+.30*repeat_sensitivity)*exposure,4)
    def decide(self,candidates:Sequence[RecommendationItem])->dict[str,Any]: return self.policy.decide(self,candidates)
    def update_state(self,decision:dict[str,Any],candidates:Sequence[RecommendationItem])->dict[str,float|int|str]:
        """Update a short-term affect model from value, novelty, repetition and session load.

        OASIS motivates environment-mediated state transitions; the coefficients
        here are explicit project assumptions so experiments can audit/tune them.
        """
        page_categories=[c for item in candidates for c in item.categories]
        repetition=sum(c in self.seen_categories for c in page_categories)/len(page_categories) if page_categories else 0.0
        c,s,b,e,f=(self.state[k] for k in ("curiosity","satisfaction","boredom","engagement","fatigue"))
        action=str(decision.get("action"))
        if action=="click":
            chosen=next(i for i in candidates if i.item_id==decision.get("item_id")); chosen_categories=set(chosen.categories)
            interest_hits=len(chosen_categories&set(self.profile.interests)); dislike_hits=len(chosen_categories&set(self.profile.disliked_categories))
            fit=clamp(interest_hits/max(1,min(len(self.profile.interests),len(chosen_categories)))-dislike_hits/max(1,len(chosen_categories)))
            novelty=len(chosen_categories-self.seen_categories)/max(1,len(chosen_categories))
            confidence=clamp(float(decision.get("confidence",.5)))
            value=clamp(.65*fit+.25*novelty+.10*confidence)
            self.behavior["click_count"]+=1; self.behavior["consecutive_clicks"]+=1; self.behavior["consecutive_skips"]=0
            click_streak=int(self.behavior["consecutive_clicks"])
            self.state["satisfaction"]=clamp(s+.30*(value-s))
            self.state["curiosity"]=clamp(c+.10*novelty-.08*fit-.03*min(click_streak,4)/4)
            self.state["boredom"]=clamp(b+.14*repetition-.16*value-.08*novelty+.025*max(0,click_streak-3))
            self.state["engagement"]=clamp(e+.20*value+.06*novelty-.08*b-.04*f)
            self.state["fatigue"]=clamp(f+.035+.015*min(click_streak,6)+.03*repetition)
            self.clicked_item_ids.add(chosen.item_id)
        else:
            value=0.0; novelty=0.0; fit=0.0
            self.behavior["next_page_count"]+=1; self.behavior["consecutive_skips"]+=1; self.behavior["consecutive_clicks"]=0
            skip_streak=int(self.behavior["consecutive_skips"]); extra=min(max(0,skip_streak-1),3)
            self.state["satisfaction"]=clamp(s-.08-.04*repetition-.02*extra)
            self.state["curiosity"]=clamp(c+.06-.06*extra-.05*repetition)
            self.state["boredom"]=clamp(b+.12+.08*repetition+.04*extra)
            self.state["engagement"]=clamp(e-.10-.04*min(skip_streak,3)-.05*repetition)
            self.state["fatigue"]=clamp(f+.04+.025*min(skip_streak,4)+.02*repetition)
        low_value=value<.45
        self.behavior["low_value_streak"]=int(self.behavior["low_value_streak"])+1 if low_value else 0
        self.behavior["high_engagement_streak"]=int(self.behavior["high_engagement_streak"])+1 if self.state["engagement"]>=.72 else 0
        if self.state["fatigue"]>=.65 and self.state["engagement"]>=.60: self.behavior["persistence_under_fatigue_steps"]+=1
        self.behavior["last_value"]=round(value,4); self.behavior["value_ema"]=round(.7*float(self.behavior["value_ema"])+.3*value,4)
        self.seen_categories.update(page_categories)
        for category in page_categories: self.category_exposures[category]=self.category_exposures.get(category,0)+1
        return {"action":action,"perceived_value":round(value,4),"preference_fit":round(fit,4),"novelty":round(novelty,4),"repetition":round(repetition,4),"consecutive_clicks":int(self.behavior["consecutive_clicks"]),"consecutive_skips":int(self.behavior["consecutive_skips"]),"low_value_streak":int(self.behavior["low_value_streak"])}

class RecommendationEnvironment:
    def __init__(self,agent:UserAgent,items:list[RecommendationItem],*,candidate_num:int,seed:int)->None:
        if candidate_num<1: raise ValueError("candidate_num must be at least 1")
        if candidate_num>len(items): raise ValueError("candidate_num cannot exceed the number of items")
        self.agent,self.items,self.candidate_num,self.seed=agent,items,candidate_num,seed; self.random=random.Random(seed)
    def run(self,track:int)->list[dict[str,Any]]:
        if track<1: raise ValueError("track must be at least 1")
        for step in range(1,track+1):
            candidates=self.random.sample(self.items,self.candidate_num); before=dict(self.agent.state); decision=self.agent.decide(candidates); transition=self.agent.update_state(decision,candidates); self.agent.memory.append({"step":step,"candidates":[asdict(i) for i in candidates],"decision":decision,"psychological_state_before":before,"psychological_state_after":dict(self.agent.state),"psychological_transition":transition,"behavioral_state":self.agent.behavioral_state()})
        return self.agent.memory

def load_json(path:Path)->Any:
    try:
        with path.open(encoding="utf-8") as f: return json.load(f)
    except FileNotFoundError as exc: raise ValueError(f"Input file not found: {path}") from exc
    except json.JSONDecodeError as exc: raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
def load_profile(path:Path)->UserProfile:
    data=load_json(path)
    if not isinstance(data,dict): raise ValueError("Profile JSON must contain an object")
    return UserProfile.from_dict(data)
def load_items(path:Path)->list[RecommendationItem]:
    data=load_json(path)
    if not isinstance(data,list) or not data: raise ValueError("Items JSON must contain a non-empty list")
    items=[RecommendationItem.from_dict(x) for x in data]
    if len({i.item_id for i in items})!=len(items): raise ValueError("Item IDs must be unique")
    return items
def load_simple_yaml(path:Path)->dict[str,Any]:
    if not path.exists(): return {}
    out={}
    for n,raw in enumerate(path.read_text(encoding="utf-8").splitlines(),1):
        line=raw.split("#",1)[0].strip()
        if not line: continue
        if ":" not in line: raise ValueError(f"Invalid YAML line {n} in {path}")
        key,value=(x.strip() for x in line.split(":",1)); value=value.strip("'\"")
        if key not in DEFAULTS: raise ValueError(f"Unknown setting '{key}' in {path}")
        out[key]=int(value) if key in {"track","candidate_num","seed"} else float(value) if key=="decision_threshold" else value
    return out
def resolve_path(value:str|Path)->Path:
    path=Path(value); return path if path.is_absolute() else ROOT/path
def save_results(base:Path,config:dict[str,Any],profile:UserProfile,memory:list[dict[str,Any]],final:dict[str,float])->Path:
    run=base/datetime.now().strftime("%Y%m%d_%H%M%S_%f"); run.mkdir(parents=True,exist_ok=False); payload={"user_profile":asdict(profile),"config":config,"trajectory":memory,"final_psychological_state":final}; (run/"memory.json").write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8"); (run/"config.json").write_text(json.dumps(config,indent=2,ensure_ascii=False)+"\n",encoding="utf-8"); clicks=[x["decision"]["title"] for x in memory if x["decision"]["action"]=="click"]
    lines=["Mini PsyBer-Agent Run","=====================",f"User profile: {profile.user_id}",f"Interests: {', '.join(profile.interests)}",f"Disliked categories: {', '.join(profile.disliked_categories)}",f"Total steps: {len(memory)}",f"Clicks: {len(clicks)}",f"Next-page actions: {len(memory)-len(clicks)}","Clicked item sequence: "+(" -> ".join(clicks) if clicks else "(none)"),"Final psychological state: "+json.dumps(final,ensure_ascii=False,sort_keys=True),""]; (run/"summary.txt").write_text("\n".join(lines),encoding="utf-8"); return run
def build_parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="Run the offline Mini PsyBer user agent."); p.add_argument("--config",default=str(ROOT/"settings/mini_agent.yaml"))
    for name in ("profile_path","items_path","output_dir"): p.add_argument("--"+name.replace("_","-"),dest=name,default=None)
    for name in ("track","candidate_num","seed"): p.add_argument("--"+name.replace("_","-"),dest=name,type=int,default=None)
    p.add_argument("--decision-threshold",type=float,default=None); return p
def main(argv:Sequence[str]|None=None)->int:
    args=build_parser().parse_args(argv)
    try:
        config=dict(DEFAULTS); config.update(load_simple_yaml(Path(args.config))); config.update({k:v for k,v in vars(args).items() if k!="config" and v is not None}); profile_path,items_path=resolve_path(config["profile_path"]),resolve_path(config["items_path"]); profile,items=load_profile(profile_path),load_items(items_path); agent=UserAgent(profile,RuleBasedPolicy(float(config["decision_threshold"]))); memory=RecommendationEnvironment(agent,items,candidate_num=int(config["candidate_num"]),seed=int(config["seed"])).run(int(config["track"])); saved=dict(config); saved.update({"profile_path":str(profile_path),"items_path":str(items_path),"output_dir":str(resolve_path(config["output_dir"])),"policy":"rule_based"}); run=save_results(resolve_path(config["output_dir"]),saved,profile,memory,agent.state)
    except (OSError,ValueError) as exc: build_parser().error(str(exc))
    print(f"Completed {len(memory)} steps. Results: {run}"); return 0
if __name__=="__main__": raise SystemExit(main())

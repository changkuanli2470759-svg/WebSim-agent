"""Cohort-directed, reproducible Persona factory inspired by TinyTroupe.

The factory separates a sampling space (cohort templates and proportions) from
individual generation. It balances categorical dimensions before adding bounded
within-group variation. No LLM or external data source is required by default.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from .mini_agent import UserProfile, clamp, resolve_path
except ImportError:
    from mini_agent import UserProfile, clamp, resolve_path

GENRES=["technology","science fiction","action","comedy","animation","family","thriller","horror","romance","drama","musical","adventure","fantasy","documentary","history","mystery","crime","war"]


def _weighted(rng:random.Random, values:dict[str,float])->str:
    return rng.choices(list(values),weights=list(values.values()),k=1)[0]


def _spread_counts(weights:dict[str,float],count:int)->list[str]:
    """Largest-remainder allocation covers cohort proportions deterministically."""
    total=sum(weights.values()); raw={k:count*v/total for k,v in weights.items()}
    counts={k:math.floor(value) for k,value in raw.items()}
    for key in sorted(weights,key=lambda k:(raw[k]-counts[k],weights[k],k),reverse=True)[:count-sum(counts.values())]: counts[key]+=1
    return [key for key in weights for _ in range(counts[key])]


def _bump(values:list[float],hours:range|list[int],amount:float)->None:
    for hour in hours: values[hour%24]+=amount


def _normalize_baseline(values:list[float])->list[float]:
    return [round(min(.95,max(.005,value)),4) for value in values]


COHORTS:dict[str,dict[str,Any]]={
    "student":{
        "description":"Students with classes, study goals and variable evening media use.",
        "age_range":[18,25],"occupations":["undergraduate student","graduate student","vocational student"],
        "interests":["science fiction","comedy","animation","technology","adventure","action","fantasy"],
        "dislikes":["history","war","musical"],"curiosity":.72,"self_control":.55,"exploration":.75,
        "baseline_peaks":{"morning":.12,"lunch":.22,"evening":.68,"late":.25},"sleep":[.0,7.0],
        "goals":[["class_am","attend class or study",.90,8.0,12.0],["class_pm","attend class or study",.88,14.0,17.0],["sleep","sleep",.95,23.5,7.0]],
        "media":{"novelty_seeking":.75,"popularity_susceptibility":.62,"autoplay_susceptibility":.58,"repetition_sensitivity":.55,"stress_coping_use":.52},
        "protective":["class schedule","peer contact","academic deadlines"]},
    "office_worker":{
        "description":"Daytime office employees balancing work, commuting, family and evening leisure.",
        "age_range":[24,55],"occupations":["office employee","engineer","teacher","administrator","service manager"],
        "interests":["drama","comedy","documentary","crime","technology","action","romance"],
        "dislikes":["horror","musical","war"],"curiosity":.50,"self_control":.70,"exploration":.48,
        "baseline_peaks":{"morning":.08,"lunch":.20,"evening":.62,"late":.12},"sleep":[23.0,6.5],
        "goals":[["work_am","work",.92,9.0,12.0],["work_pm","work",.92,13.5,18.0],["sleep","sleep",.97,23.0,6.5]],
        "media":{"novelty_seeking":.45,"popularity_susceptibility":.42,"autoplay_susceptibility":.46,"repetition_sensitivity":.62,"stress_coping_use":.55},
        "protective":["fixed work schedule","family responsibility","planned bedtime"]},
    "shift_worker":{
        "description":"Shift and night workers whose normal active hours differ from daytime norms.",
        "age_range":[22,58],"occupations":["night-shift nurse","security worker","logistics worker","factory shift worker"],
        "interests":["action","thriller","comedy","crime","science fiction","documentary"],
        "dislikes":["musical","romance","family"],"curiosity":.58,"self_control":.58,"exploration":.56,
        "baseline_peaks":{"morning":.32,"lunch":.10,"evening":.18,"late":.58},"sleep":[8.0,15.0],
        "goals":[["day_sleep","sleep",.97,8.0,15.0],["night_work","work shift",.92,22.0,6.0]],
        "media":{"novelty_seeking":.58,"popularity_susceptibility":.38,"autoplay_susceptibility":.60,"repetition_sensitivity":.50,"stress_coping_use":.64},
        "protective":["shift handover","daytime sleep plan","coworker contact"]},
    "retired":{
        "description":"Retired adults with flexible daytime schedules and comparatively stable content preferences.",
        "age_range":[60,78],"occupations":["retired teacher","retired technician","retired service worker"],
        "interests":["history","documentary","drama","family","comedy","romance"],
        "dislikes":["horror","thriller","war"],"curiosity":.38,"self_control":.76,"exploration":.32,
        "baseline_peaks":{"morning":.28,"lunch":.18,"evening":.48,"late":.04},"sleep":[22.0,6.0],
        "goals":[["exercise","exercise or outdoor activity",.80,7.0,9.0],["family","family or community activity",.78,15.0,17.0],["sleep","sleep",.97,22.0,6.0]],
        "media":{"novelty_seeking":.28,"popularity_susceptibility":.30,"autoplay_susceptibility":.35,"repetition_sensitivity":.42,"stress_coping_use":.30},
        "protective":["stable sleep routine","family contact","community activity"]},
}

DEFAULT_MIX={"student":.30,"office_worker":.40,"shift_worker":.15,"retired":.15}
GENDERS={"female":.49,"male":.49,"nonbinary":.02}
RESIDENCES={"urban":.65,"suburban":.22,"rural":.13}
HOUSEHOLDS={"alone":.24,"with_family":.58,"shared":.18}


class PersonaFactory:
    def __init__(self,cohort_mix:dict[str,float]|None=None,seed:int=42,cohort_definitions:dict[str,dict[str,Any]]|None=None)->None:
        self.cohorts=copy.deepcopy(cohort_definitions or COHORTS)
        self.cohort_mix=cohort_mix or DEFAULT_MIX
        unknown=set(self.cohort_mix)-set(self.cohorts)
        if unknown: raise ValueError("Unknown cohorts: "+", ".join(sorted(unknown)))
        if not self.cohort_mix or any(value<=0 for value in self.cohort_mix.values()): raise ValueError("Cohort weights must be positive")
        self.seed=seed

    def generate(self,count:int,id_prefix:str="agent")->list[dict[str,Any]]:
        if count<1: raise ValueError("count must be at least 1")
        cohort_plan=_spread_counts(self.cohort_mix,count)
        random.Random(f"{self.seed}:cohort-order").shuffle(cohort_plan)
        profiles=[self._person(index+1,cohort,id_prefix) for index,cohort in enumerate(cohort_plan)]
        return profiles

    def _person(self,index:int,cohort:str,id_prefix:str)->dict[str,Any]:
        spec=self.cohorts[cohort]; rng=random.Random(f"{self.seed}:{cohort}:{index}")
        age=rng.randint(*spec["age_range"]); occupation=rng.choice(spec["occupations"])
        gender=_weighted(rng,GENDERS); residence=_weighted(rng,RESIDENCES); household=_weighted(rng,HOUSEHOLDS)
        openness=clamp(spec["exploration"]+rng.uniform(-.16,.16)); conscientiousness=clamp(spec["self_control"]+rng.uniform(-.15,.15)); neuroticism=clamp(.46+rng.uniform(-.22,.22)); extraversion=clamp(.50+rng.uniform(-.24,.24)); agreeableness=clamp(.60+rng.uniform(-.18,.18))
        self_control=clamp(.72*conscientiousness+.28*spec["self_control"]+rng.uniform(-.08,.08))
        exploration=clamp(.68*openness+.32*spec["exploration"]+rng.uniform(-.06,.06))
        curiosity=clamp(.62*exploration+.38*spec["curiosity"]+rng.uniform(-.08,.08))
        interests=rng.sample(spec["interests"],k=min(3,len(spec["interests"])))
        dislikes=rng.sample(spec["dislikes"],k=min(2,len(spec["dislikes"])))
        media={key:clamp(float(value)+rng.uniform(-.14,.14)) for key,value in spec["media"].items()}
        social_support=clamp(.72 if household=="with_family" else .48 if household=="shared" else .34+rng.uniform(-.12,.12))
        baseline=self._baseline(spec,rng,media)
        goals=[{"goal_id":g[0],"name":g[1],"category":"routine","priority":clamp(g[2]+rng.uniform(-.04,.04)),"start_hour":g[3],"end_hour":g[4]} for g in spec["goals"]]
        personality=f"{cohort.replace('_',' ')}; openness {openness:.2f}; conscientiousness {conscientiousness:.2f}; social susceptibility {media['popularity_susceptibility']:.2f}"
        summary=f"A {age}-year-old {occupation} in a {residence} area, living {household.replace('_',' ')}, with {('high' if self_control>=.67 else 'moderate' if self_control>=.4 else 'low')} self-control and {('high' if exploration>=.67 else 'moderate' if exploration>=.4 else 'low')} exploration tendency."
        return {
            "user_id":f"{id_prefix}_{index:05d}","cohort":cohort,"identity_summary":summary,
            "demographics":{"age":age,"age_group":self._age_group(age),"gender":gender,"residence_type":residence,"household":household},
            "occupation":{"title":occupation,"schedule_type":"night_shift" if cohort=="shift_worker" else "daytime" if cohort in {"student","office_worker"} else "flexible"},
            "interests":interests,"disliked_categories":dislikes,"personality":personality,
            "personality_traits":{"openness":openness,"conscientiousness":conscientiousness,"extraversion":extraversion,"agreeableness":agreeableness,"neuroticism":neuroticism},
            "curiosity":curiosity,"initial_satisfaction":clamp(.52+rng.uniform(-.12,.12)),"initial_boredom":clamp(.20+rng.uniform(-.10,.12)),"exploration_tendency":exploration,"self_control":self_control,
            "lifestyle":{"sleep_window":{"start":spec["sleep"][0],"end":spec["sleep"][1]},"stress_level":clamp(.48+.35*neuroticism+rng.uniform(-.15,.1)),"social_support":social_support,"daily_structure":.82 if cohort in {"student","office_worker"} else .60},
            "media_behavior":media,"social_context":{"household":household,"support_level":social_support,"peer_influence":media["popularity_susceptibility"]},
            "long_term_goals":[g[1] for g in spec["goals"]],"protective_factors":spec["protective"],"hourly_activity_baseline":baseline,"goals":goals,
            "generation_metadata":{"method":"cohort_sampling_plan_v1","seed":self.seed,"cohort_description":spec["description"]},
        }

    def _baseline(self,spec:dict[str,Any],rng:random.Random,media:dict[str,float])->list[float]:
        values=[.015+rng.uniform(0,.015) for _ in range(24)]; p=spec["baseline_peaks"]
        _bump(values,range(6,10),p["morning"]); _bump(values,range(11,14),p["lunch"]); _bump(values,range(18,23),p["evening"]); _bump(values,[23,0,1,2],p["late"])
        intensity=.72+.42*media["autoplay_susceptibility"]+.18*media["stress_coping_use"]
        return _normalize_baseline([value*intensity+rng.uniform(-.025,.025) for value in values])

    @staticmethod
    def _age_group(age:int)->str:
        return "18-24" if age<25 else "25-44" if age<45 else "45-59" if age<60 else "60+"


def summarize(profiles:list[dict[str,Any]])->dict[str,Any]:
    return {"count":len(profiles),"cohorts":dict(Counter(p["cohort"] for p in profiles)),"age_groups":dict(Counter(p["demographics"]["age_group"] for p in profiles)),"gender":dict(Counter(p["demographics"]["gender"] for p in profiles)),"mean_self_control":round(sum(p["self_control"] for p in profiles)/len(profiles),4),"mean_exploration":round(sum(p["exploration_tendency"] for p in profiles)/len(profiles),4),"method":"cohort_sampling_plan_v1"}


def parse_mix(text:str)->dict[str,float]:
    result={}
    for part in text.split(","):
        try: name,value=part.split("=",1); result[name.strip()]=float(value)
        except ValueError as exc: raise ValueError("cohort-mix must look like student=0.3,office_worker=0.4") from exc
    return result


def load_population_spec(path:Path)->tuple[dict[str,float],dict[str,dict[str,Any]]]:
    try: payload=json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc: raise ValueError(f"Population specification not found: {path}") from exc
    except json.JSONDecodeError as exc: raise ValueError(f"Invalid population specification: {exc}") from exc
    if not isinstance(payload,dict) or not isinstance(payload.get("cohort_mix"),dict): raise ValueError("Population specification must contain cohort_mix")
    definitions=copy.deepcopy(COHORTS)
    overrides=payload.get("cohort_overrides",{})
    if not isinstance(overrides,dict): raise ValueError("cohort_overrides must be an object")
    for cohort,changes in overrides.items():
        if cohort not in definitions: raise ValueError(f"Unknown cohort override: {cohort}")
        if not isinstance(changes,dict): raise ValueError(f"Override for {cohort} must be an object")
        for key,value in changes.items():
            if key=="media" and isinstance(value,dict): definitions[cohort][key].update(value)
            elif key=="baseline_peaks" and isinstance(value,dict): definitions[cohort][key].update(value)
            else: definitions[cohort][key]=value
    return {str(k):float(v) for k,v in payload["cohort_mix"].items()},definitions


def main()->int:
    parser=argparse.ArgumentParser(description="Generate targeted, reproducible PsyBer personas.")
    parser.add_argument("--count",type=int,default=100); parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--cohort",choices=sorted(COHORTS),default=None,help="Generate only one target cohort.")
    parser.add_argument("--cohort-mix",default=None,help="Example: student=0.4,office_worker=0.6")
    parser.add_argument("--population-spec",default=None,help="Editable JSON with cohort_mix and optional cohort_overrides.")
    parser.add_argument("--id-prefix",default="agent"); parser.add_argument("--output",default="data/multi_agent/generated_profiles.json")
    args=parser.parse_args()
    try:
        if sum(value is not None for value in (args.cohort,args.cohort_mix,args.population_spec))>1: raise ValueError("Use only one of --cohort, --cohort-mix or --population-spec")
        if args.population_spec: mix,definitions=load_population_spec(resolve_path(args.population_spec))
        else: mix={args.cohort:1.0} if args.cohort else parse_mix(args.cohort_mix) if args.cohort_mix else DEFAULT_MIX; definitions=COHORTS
        profiles=PersonaFactory(mix,args.seed,definitions).generate(args.count,args.id_prefix)
        output=resolve_path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(profiles,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        summary_path=output.with_name(output.stem+"_summary.json"); summary_path.write_text(json.dumps(summarize(profiles),indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    except (OSError,ValueError) as exc: parser.error(str(exc))
    print(f"Generated {len(profiles)} profiles: {output}")
    return 0


if __name__=="__main__": raise SystemExit(main())

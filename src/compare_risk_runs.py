"""Paired recommendation-condition analysis for problematic-use reports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .problematic_use import paired_recommendation_effect
except ImportError:
    from problematic_use import paired_recommendation_effect


def _read(path: Path, name: str) -> dict:
    file_path=path/name
    if not file_path.exists(): raise ValueError(f"Missing {file_path}")
    return json.loads(file_path.read_text(encoding="utf-8"))


def compare(control_dir: Path,treatment_dir: Path,threshold:float=.15)->dict:
    control_config=_read(control_dir,"config.json"); treatment_config=_read(treatment_dir,"config.json")
    mismatches=[key for key in ("seed","agent_num","profiles_path") if control_config.get(key)!=treatment_config.get(key)]
    if mismatches: raise ValueError("Paired runs must share " + ", ".join(mismatches))
    control=_read(control_dir,"problematic_use_report.json")["agents"]
    treatment=_read(treatment_dir,"problematic_use_report.json")["agents"]
    result=paired_recommendation_effect(control,treatment,threshold)
    result.update({"control_condition":control_config.get("recommendation_condition"),"treatment_condition":treatment_config.get("recommendation_condition"),"matched_seed":control_config.get("seed")})
    return result


def main()->int:
    parser=argparse.ArgumentParser(description="Compare matched control and personalized/social Agent runs.")
    parser.add_argument("control_run"); parser.add_argument("treatment_run")
    parser.add_argument("--effect-threshold",type=float,default=.15); parser.add_argument("--output",default=None)
    args=parser.parse_args()
    try: result=compare(Path(args.control_run),Path(args.treatment_run),args.effect_threshold)
    except (OSError,ValueError,json.JSONDecodeError) as exc: parser.error(str(exc))
    output=Path(args.output) if args.output else Path(args.treatment_run)/"recommendation_effect.json"
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(f"Paired recommendation-effect report: {output}")
    return 0


if __name__=="__main__": raise SystemExit(main())

from __future__ import annotations
from pathlib import Path
import yaml

def load_policy(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f: return yaml.safe_load(f)

def decide(metrics: dict, horizon: str, policy: dict, minimum_observations: int=60) -> dict:
    if metrics.get("observations",0)<minimum_observations:
        return {"decision":"INSUFFICIENT DATA","reasons":[f"Only {metrics.get('observations',0)} usable returns; {minimum_observations} required."],"checks":[]}
    profile=policy[horizon]; checks=[]; hard_fail=False; review=False
    for item in profile["checks"]:
        value=metrics.get(item["metric"])
        if value is None or not isinstance(value,(int,float)) or value!=value:
            review=True; status="WARNING"; reason="Metric unavailable"
        else:
            passed = value <= item["limit"] if item["direction"]=="max" else value >= item["limit"]
            status="PASS" if passed else ("FAIL" if item.get("hard",False) else "WARNING")
            reason=item["description"]
            hard_fail |= status=="FAIL"; review |= status=="WARNING"
        checks.append({"metric":item["metric"],"actual":value,"threshold":item["limit"],"status":status,"explanation":reason})
    decision="FAIL" if hard_fail else "REVIEW" if review else "PASS"
    return {"decision":decision,"interpretation":"Configurable risk-screening classification only; not a prediction, guarantee, or investment recommendation.","reasons":[c["metric"]+": "+c["status"] for c in checks],"checks":checks}

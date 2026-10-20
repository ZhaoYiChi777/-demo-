"""Small live integration check on frozen material; leaves saved app runs untouched."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from run_small_baseline import ROOT, CapturedGateway, capture_open, summarize_usage, write_json
from dotenv import dotenv_values
from fsr_rubric import POLICY, scoring_rules
from hybrid_retrieval import HybridRetriever
from semantic_judgment import SemanticJudge


def main():
    folder=ROOT/'artifacts/review-generalization-20260923'
    folder.mkdir(exist_ok=True)
    case=next(c for c in json.loads((ROOT/'artifacts/small-baseline-20260923/inputs.json').read_text(encoding='utf-8')) if c['case_id']=='R03')
    rules=[r for r in case['rules']['rules'] if r['id']=='TUR-TEC-002']
    rules += [r for r in scoring_rules({'id':'safety'}) if r['id'] in ('FSR-C3','FSR-C7')]
    values=dotenv_values(ROOT/'.env')
    config=dict(enabled=True,key=values['DASHSCOPE_API_KEY'],url=values['ALIYUN_BASE_URL'].rstrip('/')+'/chat/completions',supports_thinking=True)
    write_json(folder/'input.json',dict(case_id='R03',material=case['material'],rules=rules,scoring_policy=POLICY,thinking=False))

    def evaluate(model):
        target=folder/model;target.mkdir()
        gateway=CapturedGateway({**config,'model':model},target)
        material=deepcopy(case['material'])
        found,retrieval=HybridRetriever(gateway).retrieve(material,deepcopy(rules))
        checks=[dict(id=r['id'],rule=r,category=r['category'],missing=[],result='pending',reason='待核验',
                     evidence=found[r['id']]['evidence'],retrieval=found[r['id']]['retrieval']) for r in deepcopy(rules)]
        judged,status=SemanticJudge(gateway).evaluate(material,checks)
        write_json(target/'judgments.json',dict(retrieval=retrieval,status=status,judgments=judged))
        result=dict(model=model,status=status['status'],checks=len(judged),
                    completed=status['evaluated_checks'],partial=status.get('partial_checks',0),
                    rejected=status['rejected_checks'],unavailable=status['unavailable_checks'],
                    criteria_completed=sum(not c.get('processing_error') for j in judged.values() for c in j.get('criteria',[])),
                    criteria_processing_errors=sum(bool(c.get('processing_error')) for j in judged.values() for c in j.get('criteria',[])),
                    **summarize_usage(target))
        write_json(target/'summary.json',result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        return result

    with patch('urllib.request.urlopen',capture_open),ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(evaluate,['deepseek-v4.1-flash','qwen3.8-flash']))
    write_json(folder/'summary.json',results)


if __name__=='__main__':main()

"""Small live money-rule check on two frozen reports and two configured models."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from run_small_baseline import ROOT, CapturedGateway, capture_open, summarize_usage, write_json
from dotenv import dotenv_values
from fsr_rubric import scoring_rules
from semantic_judgment import SemanticJudge


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    folder=args.output.resolve()
    folder.mkdir(parents=True,exist_ok=False)
    cases=[c for c in json.loads((ROOT/'artifacts/small-baseline-20260923/inputs.json').read_text(encoding='utf-8')) if c['case_id'] in ('R03','R09')]
    values=dotenv_values(ROOT/'.env')
    config=dict(enabled=True,key=values['DASHSCOPE_API_KEY'],url=values['ALIYUN_BASE_URL'].rstrip('/')+'/chat/completions',supports_thinking=True)
    write_json(folder/'input.json',dict(cases=cases,rule='FSR-C7',method='fixed-source rule integration check; no full-report score',thinking=False))

    def evaluate(pair):
        case,model=pair
        target=folder/(case['case_id']+'-'+model);target.mkdir()
        gateway=CapturedGateway({**config,'model':model},target)
        rule=next(r for r in scoring_rules({'id':case['branch']}) if r['id']=='FSR-C7')
        check=dict(id=rule['id'],rule=rule,category=rule['category'],missing=[],result='pending',reason='待核验',
                   evidence=deepcopy(case['material']['chunks']),retrieval=dict(status='complete',coverage_policy='per-rule-window-v1'))
        judged,status=SemanticJudge(gateway).evaluate(deepcopy(case['material']),[check])
        write_json(target/'judgments.json',dict(status=status,judgments=judged))
        criterion=judged['FSR-C7']['criteria'][0]
        result=dict(case_id=case['case_id'],model=model,result=criterion['result'],
                    processing_error=criterion.get('processing_error'),arithmetic=criterion.get('arithmetic'),
                    **summarize_usage(target))
        write_json(target/'summary.json',result)
        print(json.dumps({k:v for k,v in result.items() if k!='arithmetic'},ensure_ascii=False),flush=True)
        return result

    with patch('urllib.request.urlopen',capture_open),ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(evaluate,[(c,m) for c in cases for m in ('deepseek-v4.1-flash','qwen3.8-flash')]))
    write_json(folder/'summary.json',results)


if __name__=='__main__':main()

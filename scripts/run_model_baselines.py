"""Frozen five-report x four-model x two-method first round, without app DB writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import shutil
import threading
import time
from unittest.mock import patch

from run_small_baseline import (ROOT, CapturedGateway, capture_open, readonly_db,
                                summarize_usage, write_json)
from dotenv import dotenv_values
import workbench as workbench_module
from workbench import Workbench, RunInput
from hybrid_retrieval import HybridRetriever
from semantic_judgment import SemanticJudge, PROMPT
from judgment_contract import validate, criterion_specs
from evidence_checks import EVIDENCE_POLICY
from fsr_rubric import POLICY
from rule_contracts import contracted

CASES=[('R03',45,'safety'),('R05',4,'safety'),('R07',1,'safety'),('R08',3,'safety'),('R09',2,'innovation')]
MODELS=['deepseek-v4.1-flash','qwen3.8-flash','kimi-k3','glm-5.2']
METHODS=['direct','system']
ORDER_SEED=20260923


class FullTextRetriever:
    def __init__(self,gateway):
        self.gateway=gateway
        self.enabled=lambda:True

    def retrieve(self,material,rules):
        status=dict(status='complete',coverage_policy='per-rule-window-v1',method='fixed_full_text',
                    total_batches=0,completed_batches=0)
        return {r['id']:dict(evidence=deepcopy(material['chunks']),retrieval=deepcopy(status)) for r in rules},status


class DirectJudge:
    """Same inputs and final validator; one generation per fixed group, no semantic audit."""
    def __init__(self,gateway):self.gateway=gateway

    def evaluate(self,material,checks):
        # Keep the system's deterministic file/presence/scope checks in both methods.
        results,_=SemanticJudge(self.gateway,lambda:False).evaluate(material,checks)
        checks=[{**c,'rule':c['rule'] if c['rule'].get('contract_version') else contracted(c['rule'])} for c in checks]
        candidates=[c for c in checks if results.get(c['id'],{}).get('status')=='disabled']
        paragraphs={p['id']:p['text'] for p in material['chunks']}
        docs={p['id']:p['document_id'] for p in material['chunks']}
        for c in candidates:
            c.update(evidence_policy=EVIDENCE_POLICY,_original_paragraphs=paragraphs,_paragraph_documents=docs)
        jobs=[]
        for mode in ('presence','manual'):
            same=[c for c in candidates if c['rule']['mode']==mode]
            if mode=='presence':
                jobs.extend([c] for c in same)
            else:
                for selected,size in (([c for c in same if len(c['rule'].get('criteria',[]))<=2],8),
                                      ([c for c in same if len(c['rule'].get('criteria',[]))>2],4)):
                    jobs.extend(selected[i:i+size] for i in range(0,len(selected),size))

        def execute(group):
            payload=dict(scope=material['scope_name'],brief=material.get('brief',''),
                paragraphs=[dict(paragraph_id=p['id'],**{k:p.get(k) for k in ('text','document_id','filename','section_path','page')}) for p in material['chunks']],
                rules=[{**{k:c['rule'].get(k) for k in ('id','name','category','mode','standard','content','criteria','review_guidance','version','applicability_condition','source_metadata')},
                        'criteria':c['missing'] if c['rule']['mode']=='presence' else c['rule'].get('criteria'),
                        'criterion_specs':criterion_specs(c),'missing':c['missing'] if c['rule']['mode']=='presence' else [],
                        'matched_fields':[t for t in c['rule'].get('terms',[]) if t not in c['missing']] if c['rule']['mode']=='presence' else [],
                        'evidence_ids':list(paragraphs)} for c in group])
            tick=time.monotonic();output={};error=None
            try:
                # The same single malformed-JSON retry as the shared gateway caller.
                response=SemanticJudge(self.gateway).ask(PROMPT,payload)
                if not isinstance(response,dict) or not isinstance(response.get('judgments'),list):
                    raise ValueError('模型未返回判定列表')
                for c in group:
                    matches=[j for j in response['judgments'] if isinstance(j,dict) and j.get('rule_id')==c['id']]
                    try:
                        if len(matches)!=1:raise ValueError('判定遗漏或重复')
                        output[c['id']]=validate(matches[0],c,paragraphs)
                    except ValueError as exc:
                        output[c['id']]=dict(status='rejected',result='pending',reason=str(exc),criteria=[])
            except Exception as exc:
                error=type(exc).__name__+(f' HTTP {exc.code}' if hasattr(exc,'code') else '')
                output={c['id']:dict(status='unavailable',result='pending',reason=error,criteria=[]) for c in group}
            return output,dict(rule_ids=[c['id'] for c in group],error=error,status='failed' if error else 'completed',duration_ms=round((time.monotonic()-tick)*1000))

        logs=[]
        with ThreadPoolExecutor(max_workers=2) as pool:
            for output,log in pool.map(execute,jobs):results.update(output);logs.append(log)
        counts={name:sum(r['status']==status for r in results.values()) for name,status in
                [('evaluated_checks','completed'),('partial_checks','partial'),('rejected_checks','rejected'),('unavailable_checks','unavailable')]}
        status=dict(status='partial' if any(counts[k] for k in ('partial_checks','rejected_checks','unavailable_checks')) else 'complete',
                    model=self.gateway.config()['model'],total_batches=len(jobs),completed_batches=sum(l['error'] is None for l in logs),batches=logs,**counts)
        return results,status


def judge_factory(gateway,enabled):
    return DirectJudge(gateway) if gateway.method=='direct' else SemanticJudge(gateway,enabled)


def prepare(folder):
    source=Workbench(readonly_db,[]);cases=[]
    for cid,did,branch in CASES:
        with readonly_db() as conn:
            row=conn.execute('SELECT d.project_id,pd.material_package_id FROM documents d JOIN document_sources s ON s.document_id=d.id JOIN project_documents pd ON pd.source_id=s.id WHERE d.id=? ORDER BY s.id LIMIT 1',(did,)).fetchone()
        material=source.materials(row['project_id'],row['material_package_id'])
        assert {c['document_id'] for c in material['chunks']}=={did},'Report scope changed'
        cases.append(dict(case_id=cid,document_id=did,branch=branch,material=material,rules=source.rules(row['project_id'],row['material_package_id'])))
    write_json(folder/'inputs.json',cases)
    tasks=[dict(run_id=f'{c}-{method}-{i+1}-01',case_id=c,model=model,method=method,repeat=1)
           for c,_,_ in CASES for i,model in enumerate(MODELS) for method in METHODS]
    random.Random(ORDER_SEED).shuffle(tasks)
    write_json(folder/'plan.json',tasks)
    write_json(folder/'config.json',dict(models=MODELS,methods=METHODS,repeats=1,expected_runs=40,scoring_policy=POLICY,
        order_seed=ORDER_SEED,generation_seed=None,temperature='provider_default',thinking=False,max_output_tokens=7000,
        report_workers=2,batch_workers=2,prepared_at=datetime.now(timezone.utc).isoformat(),
        note='Shared parsed text and final validator; no new visual recognition, no expert gold labels; report score is not model accuracy.'))
    code=folder/'source';code.mkdir(exist_ok=True)
    for path in (ROOT/'backend').glob('*.py'):
        if not path.name.startswith('test_'):shutil.copy2(path,code/path.name)
    for name in ('run_model_baselines.py','run_small_baseline.py'):
        shutil.copy2(ROOT/'scripts'/name,code/name)


def evaluate(task,case,config,folder):
    target=folder/task['run_id'];target.mkdir()
    gateway=CapturedGateway({**config,'model':task['model']},target);gateway.method=task['method']
    wb=Workbench(readonly_db,[])
    wb.materials=lambda *a,**kw:deepcopy(case['material'])
    wb.rules=lambda *a,**kw:deepcopy(case['rules'])
    wb.save_run=lambda run:write_json(target/'run.json',run)
    wb.retriever=FullTextRetriever(gateway) if task['method']=='direct' else HybridRetriever(gateway,lambda:True)
    row={**task,'report':case['material']['scope_name'],'started_at':datetime.now(timezone.utc).isoformat()}
    write_json(target/'task.json',row)
    print('START '+task['run_id'],flush=True);started=time.monotonic()
    try:
        run=wb.run(case['material']['project']['id'],RunInput(material_package_id=case['material']['material_package_id'],scoring_branch=case['branch']))
        run.update(baseline_run=task)
        if task['method']=='direct':
            for check in run['checks']:check['method']='共享全文，固定分组直接评审；统一原文校验与计分'
        write_json(target/'run.json',run)
        q=run['quality_score'];j=run['judgment']
        row.update(execution_status='completed',score=q['total'],raw_score=q['raw_total'],upper_bound=q['upper_bound'],
                   comparable=q.get('comparable'),processing_pending_points=q.get('processing_pending_points'),
                   evidence_pending_points=q.get('evidence_pending_points'),coverage=q['coverage'],
                   provisional=q['provisional'],conclusion=run['project_conclusion']['label'],
                   retrieval_status=run['retrieval']['status'],judgment_status=j['status'],
                   judgment_rejected=j.get('rejected_checks',0),judgment_partial=j.get('partial_checks',0),
                   judgment_unavailable=j.get('unavailable_checks',0),**run['counts'])
    except Exception as exc:
        row.update(execution_status='failed',error=type(exc).__name__)
    row.update(elapsed_seconds=round(time.monotonic()-started,2),**summarize_usage(target))
    write_json(target/'summary.json',row)
    print('DONE '+json.dumps(row,ensure_ascii=False),flush=True)
    return row


def summarize(folder,plan):
    rows=[json.loads(p.read_text(encoding='utf-8')) for p in folder.glob('*/summary.json') if p.parent.name in {t['run_id'] for t in plan}]
    rows.sort(key=lambda r:(r['case_id'],r['model'],r['method']))
    write_json(folder/'summary.json',rows)
    if rows:
        fields=list(dict.fromkeys(k for row in rows for k in row))
        with (folder/'summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    write_json(folder/'progress.json',dict(planned=len(plan),finished=len(rows),completed=sum(r['execution_status']=='completed' for r in rows),
        failed=sum(r['execution_status']=='failed' for r in rows),api_calls=sum(r['api_calls'] for r in rows),updated_at=datetime.now(timezone.utc).isoformat()))
    return rows


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--resume',action='store_true')
    args=parser.parse_args();folder=args.output.resolve();folder.mkdir(parents=True,exist_ok=True)
    if not (folder/'inputs.json').exists():prepare(folder)
    cases={c['case_id']:c for c in json.loads((folder/'inputs.json').read_text(encoding='utf-8'))}
    plan=json.loads((folder/'plan.json').read_text(encoding='utf-8'))
    if args.prepare_only:
        print(json.dumps(dict(reports=len(cases),runs=len(plan),models=MODELS),ensure_ascii=False));return
    pending=[t for t in plan if not (folder/t['run_id']/'summary.json').exists()]
    if not args.resume and len(pending)!=len(plan):raise ValueError('Output already contains runs; use --resume')
    # Never overwrite calls from an interrupted run; keep the attempt as a separate record.
    for task in pending:
        existing=folder/task['run_id']
        if existing.exists():existing.rename(folder/(task['run_id']+'-interrupted-'+datetime.now().strftime('%H%M%S')))
    values=dotenv_values(ROOT/'.env')
    config=dict(enabled=True,key=values['DASHSCOPE_API_KEY'],url=values['ALIYUN_BASE_URL'].rstrip('/')+'/chat/completions',supports_thinking=True)
    with patch('urllib.request.urlopen',capture_open),patch.object(workbench_module,'SemanticJudge',judge_factory),ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(evaluate,t,cases[t['case_id']],config,folder) for t in pending]
        for future in as_completed(futures):future.result();summarize(folder,plan)
    summarize(folder,plan)


if __name__=='__main__':main()

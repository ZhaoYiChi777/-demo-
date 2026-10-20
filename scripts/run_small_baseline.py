"""Six real system runs on three frozen reports; never changes application data."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit, urlencode
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from dotenv import dotenv_values
from workbench import Workbench, RunInput
from hybrid_retrieval import HybridRetriever
from model_gateway import ModelGateway

CASES = [('R03',45,'safety'), ('R05',4,'safety'), ('R09',2,'innovation')]
MODELS = ['deepseek-v4.1-flash','qwen3.8-flash']
LOCAL = threading.local()
ORIGINAL_OPEN = urllib.request.urlopen


def write_json(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


@contextmanager
def readonly_db():
    conn=sqlite3.connect((ROOT/'backend/data/review.db').as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    try:yield conn
    finally:conn.close()


class CapturedResponse:
    def __init__(self,response,record,path,started):
        self.response,self.record,self.path,self.started=response,record,path,started

    def __enter__(self):
        self.response.__enter__()
        return self

    def __iter__(self):
        for line in self.response:
            if line.startswith(b'data:'):
                value=line[5:].strip()
                if value!=b'[DONE]':
                    try:
                        data=json.loads(value)
                        if data.get('usage'):self.record['usage']=data['usage']
                        if data.get('model'):self.record['returned_model']=data['model']
                        if data.get('id'):self.record['request_id']=data['id']
                        for choice in data.get('choices',[]):
                            self.record['answer']+=choice.get('delta',{}).get('content') or ''
                            if choice.get('finish_reason'):self.record['finish_reason']=choice['finish_reason']
                    except (ValueError,TypeError):pass
            yield line

    def __exit__(self,typ,value,tb):
        self.record['elapsed_seconds']=round(time.monotonic()-self.started,3)
        self.record['error']=typ.__name__ if typ else None
        write_json(self.path,self.record)
        return self.response.__exit__(typ,value,tb)


def capture_open(request,*args,**kwargs):
    gateway=LOCAL.gateway
    payload=json.loads(request.data)
    payload['stream_options']={'include_usage':True}
    request.data=json.dumps(payload,ensure_ascii=False).encode()
    with gateway.lock:
        gateway.count+=1
        path=gateway.folder/f'call-{gateway.count:03d}.json'
    record={'started_at':datetime.now(timezone.utc).isoformat(),'request':payload,'answer':'','usage':None}
    started=time.monotonic()
    try:
        response=ORIGINAL_OPEN(request,*args,**kwargs)
        return CapturedResponse(response,record,path,started)
    except Exception as exc:
        record.update(error=f'HTTP {exc.code}' if hasattr(exc,'code') else type(exc).__name__,elapsed_seconds=round(time.monotonic()-started,3))
        write_json(path,record)
        raise


class CapturedGateway(ModelGateway):
    def __init__(self,config,folder):
        super().__init__(lambda:dict(config))
        self.folder,self.count,self.lock=folder,0,threading.Lock()

    def events(self,body):
        LOCAL.gateway=self
        yield from super().events(body)


def summarize_usage(folder):
    calls=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(folder.glob('call-*.json'))]
    usages=[c['usage'] for c in calls if c.get('usage')]
    return dict(api_calls=len(calls),usage_calls=len(usages),
                input_tokens=sum(u.get('prompt_tokens',0) for u in usages),
                output_tokens=sum(u.get('completion_tokens',0) for u in usages),
                cached_input_tokens=sum((u.get('prompt_tokens_details') or {}).get('cached_tokens',0) for u in usages),
                call_errors=sum(bool(c.get('error')) for c in calls))


def evaluate(case,model,config,out):
    key=case['case_id']+'-'+model
    folder=out/key
    folder.mkdir()
    gateway=CapturedGateway({**config,'model':model},folder)
    wb=Workbench(readonly_db,[])
    wb.materials=lambda *a,**kw:deepcopy(case['material'])
    wb.rules=lambda *a,**kw:deepcopy(case['rules'])
    wb.save_run=lambda run:write_json(folder/'run.json',run)
    wb.retriever=HybridRetriever(gateway,lambda:True)
    started=time.monotonic()
    print('START '+key,flush=True)
    row=dict(case_id=case['case_id'],report=case['material']['scope_name'],model=model,repeat=1)
    try:
        run=wb.run(case['material']['project']['id'],RunInput(material_package_id=case['material']['material_package_id'],scoring_branch=case['branch']))
        score=run['quality_score']
        row.update(score=score['total'],raw_score=score['raw_total'],upper_bound=score['upper_bound'],
                   unresolved_points=round(sum(d['unresolved'] for d in score['dimensions']),2),
                   coverage=score['coverage'],provisional=score['provisional'],
                   conclusion=run['project_conclusion']['label'],
                   retrieval_status=run['retrieval']['status'],judgment_status=run['judgment']['status'],
                   judgment_rejected=run['judgment'].get('rejected_checks',0),**run['counts'])
    except Exception as exc:
        row['error']=type(exc).__name__
    row.update(elapsed_seconds=round(time.monotonic()-started,2),**summarize_usage(folder))
    write_json(folder/'summary.json',row)
    print('DONE '+json.dumps(row,ensure_ascii=False),flush=True)
    return row


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    out=args.output.resolve()
    out.mkdir(parents=True,exist_ok=True)
    values=dotenv_values(ROOT/'.env')
    base=values['ALIYUN_BASE_URL'].rstrip('/')
    config=dict(enabled=True,key=values['DASHSCOPE_API_KEY'],url=base+'/chat/completions',supports_thinking=True)
    source=Workbench(readonly_db,[])
    cases=[]
    for cid,did,branch in CASES:
        with readonly_db() as conn:
            ref=conn.execute('SELECT d.project_id,pd.material_package_id FROM documents d JOIN document_sources s ON s.document_id=d.id JOIN project_documents pd ON pd.source_id=s.id WHERE d.id=?',(did,)).fetchone()
        material=source.materials(ref['project_id'],ref['material_package_id'])
        assert {c['document_id'] for c in material['chunks']}=={did},'Unexpected material scope'
        rules=source.rules(ref['project_id'],ref['material_package_id'])
        cases.append(dict(case_id=cid,document_id=did,branch=branch,material=material,rules=rules))
    write_json(out/'inputs.json',cases)
    # Freeze the source used for all six runs, for later interpretation and reruns.
    code=out/'source';code.mkdir(exist_ok=True)
    for name in ['workbench.py','model_gateway.py','hybrid_retrieval.py','semantic_judgment.py','fsr_rubric.py','quality_scoring.py','evidence_checks.py','review_conclusion.py','rule_contracts.py','atomic_contracts.py']:
        (code/name).write_bytes((ROOT/'backend'/name).read_bytes())
    write_json(out/'config.json',dict(models=MODELS,method='current-system',repeat=1,parallel_reports=2,
               started_at=datetime.now(timezone.utc).isoformat(),url=config['url'],thinking=False,seed=None,
               note='Three-report exploratory comparison; no expert accuracy labels; no score adjustment.'))
    if args.prepare_only:
        print(json.dumps([dict(case_id=c['case_id'],chunks=len(c['material']['chunks']),rules=len(c['rules']['rules'])) for c in cases]))
        return
    endpoint=urlsplit(base)
    prices=[]
    for model in MODELS:
        url=f'{endpoint.scheme}://{endpoint.netloc}/api/v1/models?'+urlencode({'model':model})
        with ORIGINAL_OPEN(urllib.request.Request(url,headers={'Authorization':'Bearer '+config['key']}),timeout=20) as response:
            prices.extend(json.load(response)['output']['models'])
    write_json(out/'prices.json',[{k:m.get(k) for k in ['model','prices']} for m in prices])
    rows=[]
    with patch('urllib.request.urlopen',capture_open),ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(evaluate,case,model,config,out) for case in cases for model in MODELS]
        for job in as_completed(jobs):
            rows.append(job.result())
            write_json(out/'summary.json',sorted(rows,key=lambda r:(r['case_id'],r['model'])))
    rows.sort(key=lambda r:(r['case_id'],r['model']))
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with (out/'summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)


if __name__=='__main__':main()

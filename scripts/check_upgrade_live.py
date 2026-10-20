"""Live acceptance: two corrected rules, failed PDFs, then one new rubric run."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import httpx
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts'/'review-upgrade-20260915'
BASE='http://127.0.0.1:8000'
PROJECT='PRJ-e789457d750c4feaa511677496798125'

def audit_case(item):
    run_id,scope,rule=item
    with httpx.Client(timeout=900) as client:
        response=client.post(f'{BASE}/api/workbench/projects/{PROJECT}/agent/audit',json=dict(run_id=run_id,material_package_id=scope,check_ids=[rule]))
        response.raise_for_status();proposal=response.json()['proposal']
        (OUT/f'live-audit-{scope}.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'scope':scope,'summary':proposal['summary'],'items':[{'name':r['name'],'after':r['after'],'reason':r['reason']} for r in proposal['rows']]},ensure_ascii=False),flush=True)
        return proposal

with ThreadPoolExecutor(max_workers=2) as pool:
    proposals=list(pool.map(audit_case,[('REV-c590a3c91151',88,'TUR-NEC-004'),('REV-58be07dfa511',91,'TUR-DOC-003')]))

conn=sqlite3.connect(ROOT/'backend'/'data'/'review.db');conn.row_factory=sqlite3.Row
for sid,scope in [(105,89),(106,90)]:
    source=dict(conn.execute('SELECT * FROM document_sources WHERE id=?',(sid,)).fetchone())
    item=dict(conn.execute('SELECT * FROM wb_group_reports WHERE project_id=? AND material_package_id=?',(PROJECT,scope)).fetchone())
    files=[p for p in (ROOT/'backend'/'data'/'uploads'/PROJECT).iterdir() if p.name.endswith(source['filename'])]
    assert len(files)==1
    with httpx.Client(timeout=900) as client,files[0].open('rb') as stream:
        response=client.post(f'{BASE}/api/workbench/batches/{item["batch_id"]}/items/{item["id"]}/upload',files={'file':(source['filename'],stream,'application/pdf')})
        response.raise_for_status();print(json.dumps({'recovered_scope':scope,'uploaded':response.json()['uploaded']},ensure_ascii=False),flush=True)
conn.close()

with httpx.Client(timeout=1200) as client:
    response=client.post(f'{BASE}/api/workbench/projects/{PROJECT}/runs',json={'material_package_id':88})
    response.raise_for_status();run=response.json()
    (OUT/'live-fsr-run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'run':run['id'],'policy':run['quality_score']['policy'],'score':run['quality_score']['total'],
        'coverage':run['quality_score']['coverage'],'counts':run['counts'],'judgment':run['judgment']['status']},ensure_ascii=False),flush=True)
assert all(p['rows'][0]['eligible'] and p['rows'][0]['after']=='pass' for p in proposals),'Corrected-rule live check did not confirm pass; inspect artifacts.'
assert run['quality_score']['policy']=='fsr-70-30-v1' and len(run['quality_score']['dimensions'])==12

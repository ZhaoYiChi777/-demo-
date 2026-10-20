"""Targeted rechecks and atomic, user-selected application of verified conclusions."""
from copy import deepcopy
import json
from uuid import uuid4
from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from rule_contracts import contracted
from semantic_judgment import SemanticJudge
from audit_policy import AUDIT_POLICY, AUDIT_PROMPT
from workbench import now, dumps


class AuditInput(BaseModel):
    run_id: str
    material_package_id: int | None = None
    check_ids: list[str] = Field(default_factory=list)
    statuses: list[Literal['pending','fail']] = Field(default_factory=lambda:['pending','fail'])


class AuditApplyInput(BaseModel):
    proposal_id: str
    material_package_id: int | None = None
    check_ids: list[str] = Field(min_length=1)


def assert_material_unchanged(wb, run):
    material = wb.materials(run['project_id'],run['material_package_id'])
    if ([(c['id'],c['text']) for c in material['chunks']] != [(c['id'],c['text']) for c in run['chunks']]
        or material['brief'] != run['brief']
        or [(s['id'],s['parse_status'],s['document_category']) for s in material['sources']] != [(s['id'],s['parse_status'],s['document_category']) for s in run['sources']]):
        raise HTTPException(409,'材料或课题要求已更新，请先重新评审，再复核新记录。')
    return material


def inspect_checks(wb, project_id, payload):
    run = wb.get_run(payload.run_id)
    if run['project_id'] != project_id or run['material_package_id'] != payload.material_package_id:
        raise HTTPException(409,'请选择当前报告范围内的评审记录。')
    if run['status'] == 'archived':
        raise HTTPException(409,'已归档记录不可修改，请重新评审。')
    material = assert_material_unchanged(wb,run)
    if not wb.retriever or not wb.retriever.enabled() or not wb.retriever.gateway.config().get('key'):
        raise HTTPException(503,'模型检索未启用，暂不能执行智能复核。')
    selected = [c for c in run['checks'] if c['id'] in payload.check_ids] if payload.check_ids else [c for c in run['checks'] if c['result'] in payload.statuses]
    if not selected or (payload.check_ids and {c['id'] for c in selected} != set(payload.check_ids)):
        raise HTTPException(422,'未找到需要复核的检查项。')
    if any(c['result'] not in ('pending','fail') for c in selected):
        raise HTTPException(422,'请选择待确认或不符合项。')
    rules = [contracted(c['rule']) for c in selected]
    # Scan the full report again; never search just the old evidence or mix report scopes.
    retrieved, retrieval = wb.retriever.retrieve(material,rules,limit=12)
    checks = []
    for original, rule in zip(selected,rules):
        found = retrieved[rule['id']]
        evidence = list(found['evidence'])
        # Include adjacent paragraphs to retain amount units, per-unit scope and table captions.
        indexes = {i for i,c in enumerate(material['chunks']) if any(e['id']==c['id'] for e in evidence)}
        for i in sorted(indexes):
            for j in (i-1,i+1):
                if 0<=j<len(material['chunks']):
                    c=material['chunks'][j]
                    if c['document_id']==material['chunks'][i]['document_id'] and not any(e['id']==c['id'] for e in evidence):
                        evidence.append({**c,'retrieval_sources':['context'],'semantic_quotes':[]})
        checks.append({**deepcopy(original),'rule':rule,'suggestion':rule['suggestion'],'evidence':evidence,'retrieval':found['retrieval']})
        if rule['mode']=='presence':
            checks[-1]['missing']=rule.get('terms',[])
    decisions, status = SemanticJudge(wb.retriever.gateway,wb.retriever.enabled).evaluate(
        material,checks,independent=True,review_instructions=AUDIT_PROMPT)
    rows = []
    for check in checks:
        decision = decisions.get(check['id'],dict(result='pending',status='skipped',reason='未获得可核验结论。',criteria=[]))
        applicable = decision.get('status')=='completed' and decision.get('independent_agreement') is True and decision['result'] in ('pass','not_applicable')
        rows.append(dict(check_id=check['id'],name=check['rule']['name'],before=check['result'],after=decision['result'],
            eligible=applicable,reason=decision['reason'],suggestion=decision.get('suggestion') or check['suggestion'],
            rule=check['rule'],judgment=decision,evidence=check['evidence'],retrieval=check['retrieval']))
    approved = sum(r['eligible'] and r['after']=='pass' for r in rows)
    omitted = sum(r['eligible'] and r['after']=='not_applicable' for r in rows)
    unresolved = len(rows)-approved-omitted
    summary = f'复核 {len(rows)} 项：{approved} 项证据充分，可确认通过；{omitted} 项明确不适用，可排除；{unresolved} 项仍需处理。尚未修改评审结果。'
    if retrieval['status'] in ('partial','unavailable') or status['status']=='unavailable' or status.get('unavailable_checks',0):
        summary += ' 模型检索或判定未完整完成，请查看各项处理原因，恢复服务后重试。'
    if status.get('rejected_checks',0):
        summary += f' 其中 {status["rejected_checks"]} 项模型输出未通过引用或格式核验，需重试该项，不能据此认定报告缺证。'
    return dict(id=uuid4().hex,kind='batch_audit',run_id=run['id'],run_updated_at=run['updated_at'],
        name='待确认与不符合项专项复核',rows=rows,summary=summary,retrieval=retrieval,judgment=status,policy=deepcopy(AUDIT_POLICY),
        created_at=now(),applied=False,applied_ids=[])


def apply_checks(wb, project_id, scope, proposal, requested):
    requested = set(requested)
    allowed = {r['check_id']:r for r in proposal['rows'] if r['eligible']}
    if not requested or not requested.issubset(allowed):
        raise HTTPException(422,'只能应用已完成双次独立核验的通过或不适用建议。')
    current = wb.get_run(proposal['run_id'])
    if current['project_id']!=project_id or current['material_package_id']!=scope:
        raise HTTPException(409,'复核建议不属于当前报告。')
    assert_material_unchanged(wb,current)
    with wb.db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        run=json.loads(conn.execute('SELECT payload FROM wb_runs WHERE id=?',(current['id'],)).fetchone()['payload'])
        if run['status']=='archived' or run['updated_at']!=proposal['run_updated_at']:
            raise HTTPException(409,'评审记录已变化，请根据最新记录重新检测。')
        for check in run['checks']:
            if check['id'] not in requested:
                continue
            row = allowed[check['id']]
            check.setdefault('history',[]).append({k:deepcopy(check.get(k)) for k in ('result','reason','suggestion','judgment','rule','reviewed_by','reviewed_at')})
            check['history'][-1].update(at=now(),audit_id=proposal['id'])
            check.update(result=row['after'],reason=row['reason'],suggestion=row['suggestion'],rule=row['rule'],
                judgment=row['judgment'],evidence=row['evidence'],retrieval=row['retrieval'],
                reviewed_by='用户确认智能复核',reviewed_at=now(),method='全范围双重检索；两次独立判定；用户批量确认')
        run['updated_at']=now()
        run.setdefault('audits',[]).append(dict(id=proposal['id'],at=now(),check_ids=sorted(requested),summary=proposal['summary']))
        wb.aggregate(run)
        conn.execute('UPDATE wb_runs SET payload=? WHERE id=?',(dumps(run),run['id']))
    return run

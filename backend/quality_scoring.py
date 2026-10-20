"""Persisted rubric versions; pending evidence never earns maturity points."""
from rule_contracts import excluded
from fsr_rubric import POLICY as FSR_POLICY, POLICIES, MAX_SCORE, COMMON, BRANCHES
from evidence_checks import EVIDENCE_POLICY
POLICY = 'demo-quality-v1'
DIMENSIONS = [('material', '材料完整性', 20), ('common', '通用规范', 25),
              ('special', '专项技术', 35), ('topic', '课题目标与验收', 20)]


def dimension_key(check):
    if check['category'] == 'common' and check['rule']['mode'] == 'presence':
        return 'material'
    return check['category']


def quality_score(run):
    if run.get('scoring_policy') in POLICIES:
        return fsr_score(run)
    dimensions = []
    for key, name, weight in DIMENSIONS:
        checks = [c for c in run['checks'] if dimension_key(c) == key and not excluded(c)]
        counts = {v: sum(c['result'] == v for c in checks) for v in ('pass', 'fail', 'pending')}
        score = 100 * (counts['pass'] + counts['pending'] * .5) / len(checks) if checks else None
        dimensions.append(dict(id=key, name=name, weight=weight, total=len(checks), **counts,
                               score=score, contribution=score * weight / 100 if score is not None else None))
    total = sum(d['contribution'] for d in dimensions) if all(d['total'] for d in dimensions) else None
    return dict(policy=POLICY, label='演示评分口径', dimensions=dimensions,
                total=int(total * 10 + .5) / 10 if total is not None else None,
                provisional=any(c['result'] == 'pending' for c in run['checks']))


def fsr_score(run):
    policy = run.get('scoring_policy', FSR_POLICY)
    branch = run.get('scoring_branch', {})
    specs = [(row, 'common') for row in COMMON]
    if branch.get('id') in BRANCHES:
        specs += [(row, 'branch') for row in BRANCHES[branch['id']][1]]
    checks = {c['id']: c for c in run['checks']}
    dimensions = []
    for (key, name, weight, names), group in specs:
        check = checks.get('FSR-'+key)
        # Recompute an old record against its own frozen criteria, never today's rubric text.
        names = check.get('rule', {}).get('criteria', names) if check else names
        omit = bool(check and excluded(check))
        judgment = check.get('judgment', {}) if check else {}
        criteria = {c['criterion']:c for c in judgment.get('criteria', [])} if judgment.get('status') in ('completed','partial') else {}
        items = []
        for criterion in names:
            item = criteria.get(criterion, {})
            result = item.get('result', 'pending')
            # Applicability is a prerequisite for earning any criterion points.
            # Older snapshots without this field retain their recorded behaviour.
            if judgment.get('applicability') == 'uncertain':
                result = 'pending'
            if check and check.get('reviewed_by') and check['result'] != 'not_applicable':
                result = check['result']
            # A processing failure cannot retain provisional pass points from a discarded draft.
            if check and not check.get('reviewed_by') and judgment.get('evidence_policy') != EVIDENCE_POLICY and judgment.get('result') == 'pending' and check.get('retrieval', {}).get('status') in ('partial', 'unavailable'):
                result = 'pending'
            reason = item.get('reason') or (check or {}).get('reason', '尚未核验')
            if check and check.get('reviewed_by'):
                reason = check.get('reason', reason)
            pending_reason = None
            if result == 'pending':
                pending_reason = 'missing_evidence' if check and check.get('reviewed_by') else item.get('pending_reason') or ('processing_error' if judgment.get('status') in ('rejected','unavailable','disabled','incomplete','disagreement') or not item else 'missing_evidence')
            if judgment.get('applicability') == 'uncertain' and not (check or {}).get('reviewed_by'):
                reason = '本条适用性尚未确定，分项暂不计已确认得分。' + (check or {}).get('reason', '')
            items.append(dict(name=criterion, result=result, reason=reason,
                              evidence=item.get('evidence', []), check_id='FSR-'+key, pending_reason=pending_reason))
        counts = {r:sum(i['result']==r for i in items) if not omit else 0 for r in ('pass','fail','pending')}
        total = len(items) if not omit else 0
        dimensions.append(dict(id=key,name=name,weight=weight,group=group,total=total,**counts,
            excluded=omit,items=items,score=100*counts['pass']/total if total else None,
            processing_pending=sum(i['result']=='pending' and i['pending_reason'] in ('processing_error','retrieval_incomplete') for i in items) if not omit else 0,
            contribution=weight*counts['pass']/total if total else 0,
            unresolved=weight*counts['pending']/total if total else 0))
    # Not-applicable dimensions are removed within their original 70/30 group.
    for group, budget in [('common',70),('branch',30)]:
        active = [d for d in dimensions if d['group']==group and not d['excluded']]
        available = sum(d['weight'] for d in active)
        for d in dimensions:
            if d['group'] == group:
                d['effective_weight'] = d['weight'] * budget / available if available and not d['excluded'] else 0
                if d['total']:
                    d['contribution'] = d['effective_weight'] * d['pass'] / d['total']
                    d['unresolved'] = d['effective_weight'] * d['pending'] / d['total']
                d['processing_unresolved'] = d['effective_weight'] * d['processing_pending'] / d['total'] if d['total'] else 0
                d['evidence_unresolved'] = d['unresolved'] - d['processing_unresolved'] if d['total'] else 0
    caps = []
    cap_specs = [('FSR-CAP49',49)]
    if policy in ('fsr-70-30-v1', 'fsr-70-30-v2'):
        cap_specs.append(('FSR-CAP69',69))
    for rid, value in cap_specs:
        c = checks.get(rid)
        if c and c['result'] == 'fail' and not excluded(c):
            caps.append(dict(limit=value,check_id=rid,reason=c['reason']))
    raw = sum(d['contribution'] for d in dimensions)
    unresolved = sum(d['unresolved'] for d in dimensions)
    maximum = MAX_SCORE if policy in ('fsr-70-30-v4','fsr-70-30-v5') else 100
    if raw > maximum:
        caps.append(dict(limit=maximum,check_id='SCORE-MAX92',reason=f'分项合计{raw:.2f}分，按统一最高{maximum}分计入最终得分。'))
    ceiling = min([maximum]+[c['limit'] for c in caps])
    known_branch = branch.get('id') in BRANCHES
    both_groups = all(any(d['group']==g and d['total'] for d in dimensions) for g in ('common','branch'))
    localized = run.get('retrieval',{}).get('coverage_policy') == 'per-rule-window-v1'
    processing_failed = (run.get('judgment',{}).get('status')=='unavailable' and not any(d['pass'] or d['fail'] for d in dimensions)) if localized else (run.get('retrieval',{}).get('status')=='unavailable' or run.get('judgment',{}).get('status')=='unavailable')
    if not localized and policy in ('fsr-70-30-v2', 'fsr-70-30-v3', 'fsr-70-30-v4','fsr-70-30-v5'):
        processing_failed = processing_failed or any(not checks.get(r,{}).get('reviewed_by') and checks.get(r,{}).get('judgment',{}).get('status') in ('rejected','unavailable','incomplete','disagreement') for r, _ in cap_specs)
    processing_points = sum(d['processing_unresolved'] for d in dimensions)
    processing_count = sum(d['processing_pending'] for d in dimensions)
    if policy == 'fsr-70-30-v5':
        processing_failed = bool(processing_count and processing_count == sum(d['total'] for d in dimensions))
    cap_processing = any(not checks.get(r,{}).get('reviewed_by') and checks.get(r,{}).get('judgment',{}).get('status') in ('partial','rejected','unavailable','incomplete','disagreement','disabled') for r,_ in cap_specs)
    computable = known_branch and both_groups and not processing_failed
    total = round(min(raw,ceiling),1) if computable else None
    provisional = bool(unresolved or not computable or any(checks.get(r,{}).get('result','pending')=='pending' for r in ('FSR-CAP49','FSR-CAP69')))
    grade = None if provisional or total is None else 'A' if total>=85 else 'B' if total>=75 else 'C' if total>=60 else 'D'
    label = {'fsr-70-30-v2':'可研阶段审查 · 共性70分＋类型分支30分 · 第2版',
             'fsr-70-30-v3':'可研阶段审查 · 共性70分＋类型分支30分 · 第3版',
             'fsr-70-30-v4':'可研阶段审查 · 共性70分＋类型分支30分 · 第4版（最高92分）',
             'fsr-70-30-v5':'可研阶段审查 · 共性70分＋类型分支30分 · 第5版（子项独立计分，最高92分）'}.get(policy, '可研成熟度 · 共性70分＋类型分支30分')
    decision_notes = []
    cap_unresolved = localized and checks.get('FSR-CAP49',{}).get('result','pending') == 'pending'
    if cap_unresolved:
        decision_notes.append(dict(check_id='FSR-CAP49',result='pending',reason=checks.get('FSR-CAP49',{}).get('reason','完整性尚未核验'),
            explanation='当前暂计已核验分项；完整可研范围尚待确认，若仅为专项篇章，最终仍按49分上限计算。'))
    consistency = checks.get('FSR-CAP69')
    if policy in ('fsr-70-30-v3', 'fsr-70-30-v4','fsr-70-30-v5') and consistency and consistency['result'] in ('fail', 'pending'):
        decision_notes.append(dict(check_id=consistency['id'], result=consistency['result'],
            reason=consistency['reason'], explanation='关键数据仍需核实或修正。按对应分项计分，不自动限制为69分；分数不代表项目已审定通过。'))
    explanation = ('第5版按子判据保留已核验结果，模型处理失败与材料缺证分列；处理未完成的评分不进入有效分数比较。最终最高92分，专项篇章49分条件上限保留。' if policy == 'fsr-70-30-v5' else
                   '第4版最终得分和可得分区间上限均不超过92分；分项仍按100分基准计算，保留原始分项合计。关键数据矛盾按分项计分并提示核验。' if policy == 'fsr-70-30-v4' else
                   '第3版取消关键数据矛盾的默认69分封顶，按分项计分并列明核验问题；项目结论继续按原规则等级汇总。' if policy == 'fsr-70-30-v3' else
                   '第2版按可研阶段核验：核心要求有依据、仅非必需低影响细节欠完善时可通过并保留建议。' if policy == 'fsr-70-30-v2' else '')
    return dict(policy=policy,label=label,branch=branch,dimensions=dimensions,
        total=total,raw_total=round(raw,1),maximum=maximum,upper_bound=round(min(raw+unresolved,ceiling),1) if computable else None,
        lower_bound=min(total,49) if cap_unresolved and total is not None else total,
        coverage=round(100*(1-unresolved/100),1) if known_branch and both_groups else None,
        provisional=provisional,grade=grade,caps=caps,decision_notes=decision_notes,cap_unresolved=cap_unresolved,
        processing_pending_points=round(processing_points,2), evidence_pending_points=round(unresolved-processing_points,2),
        processing_pending_count=processing_count, comparable=bool(computable and not processing_points and not cap_processing and not cap_unresolved),
        scoring_status='processing_failed' if processing_failed else 'processing_partial' if processing_points or cap_processing else 'pending' if provisional else 'completed',
        explanation=explanation + '符合获得该判据分值，不符合不得分；待确认不计已确认得分，其分值单列为未决。旧原子规则和课题要求用于核验，不重复扣入成熟度分。等级待证据及封顶核验完成后确定。')

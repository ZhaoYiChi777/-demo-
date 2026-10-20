"""Client's four-level opinion, separate from individual compliance and scores."""
LABELS = ['一致通过', '少量修改后通过', '大量修改后通过', '不通过']
METHOD = ('按原子规则原有问题级别汇总：0级为支撑，1级少量修改，2级大量修改，'
          '3级仅在否决事实已核实时不通过；待确认不等于否决。评分、课题核验和逐项符合性单独保留。')


def project_conclusion(checks):
    issues, unresolved = [], []
    for check in checks:
        rule = check['rule']
        meta = rule.get('source_metadata', {})
        level = meta.get('severity')
        result = check['result']
        if result == 'not_applicable':
            continue
        if result == 'pending':
            unresolved.append({'id': check['id'], 'name': rule['name'], 'level': level,
                               'reason': check.get('reason', '')})
        if level is None or level == 0:
            continue
        judgment = check.get('judgment', {})
        confirmed = result == 'fail'
        criteria = judgment.get('criteria', [])
        material_gap = any(c['result']=='pending' and c.get('pending_reason') not in ('processing_error','retrieval_incomplete') for c in criteria) if criteria else judgment.get('status')=='completed'
        evidence_gap = (result == 'pending' and judgment.get('status') in ('completed','partial') and material_gap
                        and judgment.get('applicability') == 'applicable' and level in (1, 2))
        if confirmed or evidence_gap:
            issues.append({'id': check['id'], 'name': rule['name'], 'level': level,
                           'basis': '已核实问题' if confirmed else '适用规则的证据待补充',
                           'reason': check.get('reason', '')})
    level = max((i['level'] for i in issues), default=0)
    # Unresolved scope or execution errors cannot be represented as unanimous approval.
    uncertain = any(i['level'] != 0 for i in unresolved)
    no_basis = not any(c['rule'].get('source_metadata') and c['result'] in ('pass', 'fail') for c in checks)
    provisional = uncertain or no_basis
    selected = None if level == 0 and provisional else level
    label = LABELS[selected] if selected is not None else '暂未形成四级结论'
    explanation = (f'依据原规则等级，当前意见为“{label}”。' if selected is not None else '尚无充分依据形成四级意见，请先完成待确认事项核验。')
    if provisional and selected is not None:
        explanation += '这是初步意见，待确认事项完成后可能调整；不表示已审定通过。'
    if issues:
        explanation += '主要依据：' + '；'.join(i['name'] + '（' + i['basis'] + '）' for i in issues if i['level'] == level) + '。'
    return dict(policy='client-four-level-v1', level=selected, label=label,
                provisional=provisional, issues=issues, unresolved=unresolved,
                explanation=explanation, method=METHOD)

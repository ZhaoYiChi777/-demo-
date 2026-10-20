"""Source-bound evidence metadata and deterministic arithmetic for rubric criteria."""
from decimal import Decimal, InvalidOperation
import re
from money_sources import expense_groups


EVIDENCE_POLICY = 'criterion-evidence-v1'
STATES = {'before', 'after', 'scope', 'unspecified'}
MONEY = {'元': Decimal(1), '万元': Decimal(10000), '亿元': Decimal(100000000)}
UNIT_ALIASES = {'万':'万元', '亿':'亿元'}
NUMBER = r'\d+(?:,\d{3})*(?:\.\d+)?'
MONEY_LITERAL = re.compile(r'(?<![\d.])(' + NUMBER + r')\s*(亿元|万元|元|亿|万)(?![/／])')


def monetary_criterion(name):
    money = any(s in name for s in ('总投资','总额','分项合计','投资组成','投资、收益','投资金额')) or ('费用' in name and any(s in name for s in ('合计','闭合')))
    comparison = any(s in name for s in ('合计','闭合','一致','矛盾','校核','差异'))
    return money and comparison


def scope_wide(name):
    return any(s in name for s in ('前后一致', '不存在', '完整且闭合', '完整可研', '未遗漏', '多版本'))


def pending(criterion, reason, pending_reason='missing_evidence'):
    criterion.update(result='pending', finding_type='missing_evidence', evidence_issue=reason,
                     reason=reason, pending_reason=pending_reason)


def amount(ref):
    value, unit = str(ref.get('value', '')).strip(), str(ref.get('unit', '')).strip()
    # Numeric operands must occur with their unit in the cited source, not only in model prose.
    quote = re.sub(r'\s+', '', ref['quote']).replace(',', '').replace('，', '')
    number = value.replace(',', '').replace('，', '')
    if not re.fullmatch(r'\d+(?:\.\d+)?', number) or not unit:
        raise ValueError('计算值或单位未明确')
    unit = UNIT_ALIASES.get(unit,unit)
    if re.search(r'(?<![\d.])' + re.escape(number + unit) + r'(?![\u4e00-\u9fff]*[/／])', quote):
        return Decimal(number), unit
    if unit in MONEY and any(Decimal(n.replace(',','')) == Decimal(number) and UNIT_ALIASES.get(u,u)==unit for n,u in MONEY_LITERAL.findall(ref['quote'])):
        return Decimal(number), unit
    # A table header is accepted only when its unit is explicit in the same quoted text.
    if unit in MONEY and re.search(r'(?:单位[:：]?|[（(])'+re.escape(unit)+r'[）)]?',quote) and re.search(r'(?<![\d.])'+re.escape(number)+r'(?![\d.])',quote):
        return Decimal(number), unit
    raise ValueError('计算值与单位未同时出现在引用原文中')


def source_expense_sum(check, paragraphs, complete=False):
    """Recover a single source expense block; completeness remains a model judgment."""
    refs = expense_groups(check, paragraphs)
    if not refs:
        return None
    spec = dict(operation='sum', operand_indexes=list(range(len(refs)-1)),
                result_index=len(refs)-1, complete=False)
    arithmetic = calculate(spec, refs)
    # A partial extraction below the total cannot establish a contradiction.
    if complete and Decimal(arithmetic['difference']) == 0:
        arithmetic = calculate({**spec, 'complete':True}, refs)
    return arithmetic, refs


def calculate(calculation, refs):
    indexes = calculation.get('operand_indexes', [])
    result_index = calculation.get('result_index')
    if not isinstance(indexes, list) or not indexes or any(type(i) is not int or i < 0 or i >= len(refs) for i in indexes):
        raise ValueError('缺少可定位的计算输入')
    if type(result_index) is not int or result_index < 0 or result_index >= len(refs) or result_index in indexes or len(set(indexes)) != len(indexes):
        raise ValueError('计算输入重复或缺少报告合计引用')
    selected = [refs[i] for i in indexes + [result_index]]
    if len({(r['paragraph_id'], r['quote'], r.get('object'), str(r.get('value','')), r.get('unit')) for r in selected}) != len(selected):
        raise ValueError('同一引用不能重复计入金额')
    states = {r.get('state') for r in selected} - {'scope', 'unspecified', None}
    if len(states) > 1:
        raise ValueError('改造前后数据不能作为同一费用口径相加')
    values = [amount(r) for r in selected]
    reported, target_unit = values[-1]
    if target_unit not in MONEY:
        raise ValueError('报告合计不是支持的金额单位')
    if calculation.get('operation') == 'sum':
        labels=[refs[i].get('object') for i in indexes]
        if all(labels) and len(set(labels)) != len(indexes):
            raise ValueError('费用明细对象重复，不能重复相加')
        if any(unit not in MONEY for _, unit in values):
            raise ValueError('费用求和的单位不兼容')
        computed = sum(v * MONEY[u] for v, u in values[:-1]) / MONEY[target_unit]
    elif calculation.get('operation') == 'multiply' and len(indexes) == 2:
        (count, count_unit), (price, price_unit) = values[:-1]
        parts = price_unit.replace('／', '/').split('/')
        if len(parts) != 2 or parts[0] not in MONEY or parts[1] != count_unit:
            raise ValueError('数量与单价单位不兼容')
        computed = count * price * MONEY[parts[0]] / MONEY[target_unit]
    else:
        raise ValueError('仅支持费用求和或数量乘单价')
    difference = computed - reported
    # Compare using the report's displayed precision, not binary floating point.
    precision = Decimal(1).scaleb(reported.as_tuple().exponent)
    matched = computed.quantize(precision) == reported
    complete = calculation.get('complete') is True
    text = f'程序核算：计算值{computed:f}{target_unit}，报告值{reported:f}{target_unit}，差额{difference:f}{target_unit}。'
    exceeds = not matched and difference > 0
    if not complete and exceeds:
        text += '已列明费用已超过报告总额；这是同一口径下的数值矛盾，不将明细合计认定为审定总投资。'
    elif not complete:
        text += '当前只定位到部分明细，不能据此认定总投资闭合或错误。'
    return dict(status='matched' if matched and complete else 'mismatch' if complete or exceeds else 'partial',
                computed=str(computed), reported=str(reported), difference=str(difference), unit=target_unit,
                operation=calculation['operation'], reason=text)


def check_criterion(criterion, check, paragraphs):
    """Do not turn an arithmetic match into approval of an entire professional criterion."""
    if check.get('evidence_policy') != EVIDENCE_POLICY:
        return
    refs = criterion['evidence']
    # These existing requirements concern the proposed design, not the old defect.
    design_safety = criterion['criterion'] in {
        '明确失电、失气或设备故障时的安全状态与保护措施',
        '拟建气动方案说明最低工作气压、单路失效及失电失气时的安全关闭措施',
        '说明失电、通信故障、执行机构卡涩时的安全位及手动应急措施',
    }
    if (design_safety and criterion['result'] == 'pass' and refs
            and all(r.get('state') == 'before' for r in refs)):
        pending(criterion, '引用仅说明改造前缺陷，尚不能证明拟建方案的故障安全状态与保护措施。')
    rubric = check['id'].startswith('FSR-')
    if rubric and criterion['result'] in ('pass', 'fail'):
        if any(not isinstance(r.get('object'), str) or not r['object'].strip() or r.get('state') not in STATES for r in refs):
            criterion.setdefault('format_warnings',[]).append('辅助对象或状态标签未完整返回；保留已核验原文与判据结论。')
        for ref in refs if not monetary_criterion(criterion['criterion']) else []:
            if ref.get('value') or ref.get('unit'):
                try:
                    amount(ref)
                except (ValueError, InvalidOperation):
                    criterion.setdefault('format_warnings',[]).append('辅助数值标签未能对应原文；数值判断以原文和程序核算为准。')
                    ref.pop('value',None);ref.pop('unit',None)
        pair = criterion.get('comparison', 'none')
        if pair not in ('none', 'before_after', 'same_basis'):
            criterion.setdefault('format_warnings',[]).append('未返回对照标签；保留判据与原文对照结果。')
        if pair != 'none':
            if pair == 'same_basis' and {'before', 'after'}.issubset({r.get('state') for r in refs}):
                pending(criterion, '引用分别属于改造前后，不能直接认定为同一口径的矛盾。')
    calculation = criterion.get('calculation')
    if not monetary_criterion(criterion['criterion']):
        if calculation:
            criterion['ignored_calculation'] = calculation
            criterion.pop('calculation',None)
            criterion.setdefault('format_warnings',[]).append('本判据不属于费用合计核算，已忽略模型附带的费用求和或乘法字段。')
        return
    can_recover = not calculation or (isinstance(calculation,dict) and calculation.get('operation') == 'sum')
    states = {r.get('state') for r in refs} - {'scope','unspecified',None}
    recovered = source_expense_sum({**check,'_criterion_refs':refs},paragraphs,
        complete=isinstance(calculation,dict) and calculation.get('complete') is True) if len(states)<=1 else None
    if recovered and recovered[0]['status']=='mismatch':
        arithmetic, source_refs = recovered
        criterion['arithmetic']={**arithmetic,'method':'原文费用字段核算','inputs':source_refs}
        for source in source_refs:
            if source not in refs:
                refs.append(source)
        if not criterion.get('evidence_issue'):
            criterion.update(result='fail',finding_type='contradiction',reason=arithmetic['reason'])
        return
    if calculation:
        try:
            if not isinstance(calculation, dict):
                raise ValueError('计算请求格式错误')
            try:
                arithmetic = calculate(calculation, refs)
            except (ValueError, InvalidOperation):
                if not recovered or not can_recover:
                    raise
                arithmetic, source_refs = recovered
                arithmetic = {**arithmetic,'method':'原文费用字段核算','inputs':source_refs}
                criterion['calculation_mapping'] = '按原文费用项重建'
                for source in source_refs:
                    if source not in refs:
                        refs.append(source)
            criterion['arithmetic'] = arithmetic
            if arithmetic['status'] == 'mismatch' and not criterion.get('evidence_issue'):
                criterion.update(result='fail', finding_type='contradiction', reason=arithmetic['reason'])
            elif arithmetic['status'] == 'partial':
                pending(criterion, arithmetic['reason'])
            elif criterion['result'] == 'fail' and not criterion.get('evidence_issue'):
                pending(criterion, arithmetic['reason'] + '计算未支持初判的数值矛盾，需核实其他依据。')
            else:
                criterion['reason'] += '\n' + arithmetic['reason']
        except (ValueError, InvalidOperation) as exc:
            criterion['calculation_error'] = str(exc)
            criterion['processing_error'] = '金额计算映射未完成：' + str(exc)
            pending(criterion, '金额计算输入尚未正确定位，需修复计算映射；这不是报告缺项：' + str(exc), 'processing_error')
    elif rubric and '完整且闭合' in criterion['criterion'] and criterion['result'] == 'pass':
        pending(criterion, '金额闭合尚缺少可复算的分项与合计引用，需补检索后核算。')


def add_neighbors(material, evidence):
    """Keep table captions and units next to matched evidence, within the same document."""
    chunks = material.get('chunks', [])
    ids = {e['id'] for e in evidence}
    for i, chunk in enumerate(chunks):
        if chunk['id'] not in ids:
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(chunks) and chunks[j].get('document_id') == chunk.get('document_id'):
                neighbor = chunks[j]
                if not any(e['id'] == neighbor['id'] for e in evidence):
                    evidence.append({**neighbor, 'retrieval_sources':['context'], 'semantic_quotes':[]})

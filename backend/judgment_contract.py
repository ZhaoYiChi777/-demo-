"""Model-neutral criterion identifiers, source references and partial judgments."""
from copy import deepcopy
import re
from evidence_checks import check_criterion, monetary_criterion
from money_sources import resolve_expense_ref


def criterion_specs(check):
    names = check.get('missing', []) if check['rule']['mode'] == 'presence' else check['rule'].get('criteria', [])
    return [dict(criterion_id=f"{check['id']}:{i+1}", criterion=name) for i, name in enumerate(names or [])]


def source_ref(ref, paragraphs, originals):
    if not isinstance(ref, dict):
        raise ValueError('引用格式错误')
    ref = dict(ref)
    pid = ref.get('paragraph_id')
    if isinstance(pid, str) and pid.isdecimal():
        pid = int(pid)
    if type(pid) is not int or pid not in paragraphs or pid not in originals:
        raise ValueError('引用段落不在本次输入范围')
    quote = ref.get('quote')
    if quote is None or quote == '':
        # Paragraph references point to the stored original even when the prompt
        # shows several windows of that paragraph separated by an omission marker.
        return {**ref, 'paragraph_id': pid, 'quote': originals[pid], 'reference_mode': 'paragraph'}
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError('引用原文为空')
    source = originals[pid]
    # Whitespace-only differences are presentation differences. Keep every original
    # word, number and punctuation mark; never fuzzy-match or join ellipsis fragments.
    def locate(text, fragment):
        if fragment in text:
            return fragment
        positions = [i for i, char in enumerate(text) if not char.isspace()]
        compact = ''.join(text[i] for i in positions)
        needle = re.sub(r'\s+', '', fragment)
        start = compact.find(needle)
        if not needle or start < 0:
            raise ValueError('引用无法在本条规则的输入原文中核验')
        return text[positions[start]:positions[start + len(needle) - 1] + 1]
    locate(paragraphs[pid], quote)
    canonical = locate(source, quote)
    if canonical != quote:
        ref['reference_mode'] = 'whitespace_normalized'
    return {**ref, 'paragraph_id': pid, 'quote': canonical}


def summarize(item, criteria, presence=False):
    errors = [dict(criterion_id=c.get('criterion_id'), criterion=c['criterion'], reason=c['processing_error'])
              for c in criteria if c.get('processing_error')]
    complete = [c for c in criteria if not c.get('processing_error')]
    status = 'partial' if errors and complete else 'rejected' if errors else 'completed'
    results = [c['result'] for c in criteria]
    result = 'pending'
    if item['applicability'] == 'not_applicable' and not errors:
        result = 'not_applicable'
    elif item['applicability'] == 'applicable':
        if 'fail' in results:
            result = 'fail'
        elif not errors and (presence or item.get('coverage_complete') is True) and all(r == 'pass' for r in results):
            result = 'pass'
    issues = [c['reason'] for c in criteria if c.get('evidence_issue') or c.get('arithmetic') or c.get('processing_error')]
    reason = '\n'.join(dict.fromkeys(issues)) if issues else item['reason']
    return {**item, 'criteria': criteria, 'result': result, 'status': status,
            'reason': reason, 'processing_errors': errors,
            'suggestion': '请核验尚未完成的子判据；已核验子项保留。' if errors else item.get('suggestion', '')}


def validate(item, check, paragraphs):
    if not isinstance(item, dict) or item.get('applicability') not in ('applicable', 'uncertain', 'not_applicable'):
        raise ValueError('缺少规则适用性说明')
    if not isinstance(item.get('reason'), str) or not item['reason'].strip():
        raise ValueError('缺少结论依据')
    originals = check.get('_original_paragraphs', paragraphs)
    supplied = item.get('criteria', [])
    if not isinstance(supplied, list):
        raise ValueError('缺少具体判据')
    specs = criterion_specs(check)
    if not specs:
        if not supplied:
            raise ValueError('缺少具体判据')
        specs = [dict(criterion_id=f"{check['id']}:{i+1}", criterion=c.get('criterion'))
                 for i, c in enumerate(supplied) if isinstance(c, dict)]
    # Scope is a rule-level decision. A model need not invent per-criterion findings
    # for equipment or conditions that are outside this report's scope.
    if item['applicability'] == 'not_applicable':
        if check['id'].startswith('FSR-C'):
            raise ValueError('共性评分与结构性封顶要求适用于所有可研，缺证不能标为不适用')
        refs = item.get('applicability_evidence', []) + [r for c in supplied if isinstance(c, dict) for r in c.get('evidence', [])]
        verified = []
        for ref in refs:
            try:
                canonical = source_ref(ref, paragraphs, originals)
                if canonical not in verified:
                    verified.append(canonical)
            except ValueError:
                continue
        if not verified:
            raise ValueError('不适用结论缺少范围原文依据')
        criteria = [{**s, 'result': 'pending', 'reason': item['reason'], 'evidence': verified,
                     'pending_reason': 'not_applicable'} for s in specs]
        return summarize(item, criteria)
    cleaned = []
    for spec in specs:
        name = spec['criterion']
        matches = [c for c in supplied if isinstance(c, dict) and
                   (c.get('criterion_id') == spec['criterion_id'] or
                    (not c.get('criterion_id') and c.get('criterion') == name))]
        if not matches and check['rule']['mode'] == 'presence':
            matches = [c for c in supplied if isinstance(c, dict) and not c.get('criterion_id') and
                       isinstance(c.get('criterion'), str) and name in c['criterion'] and
                       sum(s['criterion'] in c['criterion'] for s in specs) == 1]
        try:
            if len(matches) != 1:
                raise ValueError('判据遗漏或重复')
            criterion = {**deepcopy(matches[0]), **spec}
            if item['applicability'] == 'applicable':
                kind = criterion.get('finding_type')
                if kind in ('supported', 'contradiction', 'missing_evidence'):
                    criterion['result'] = {'supported': 'pass', 'contradiction': 'fail', 'missing_evidence': 'pending'}[kind]
            if not isinstance(name, str) or not name.strip() or criterion.get('result') not in ('pass', 'fail', 'pending'):
                raise ValueError('判据结果格式错误')
            if not isinstance(criterion.get('reason'), str) or not criterion['reason'].strip():
                raise ValueError('缺少判据依据说明')
            refs = criterion.get('evidence', [])
            if not isinstance(refs, list) or (criterion['result'] != 'pending' and not refs):
                raise ValueError('明确结论缺少原文引用')
            verified = []
            for ref in refs:
                try:
                    if monetary_criterion(name):
                        ref = resolve_expense_ref(ref, paragraphs, check.get('_paragraph_documents'))
                    verified.append(source_ref(ref, paragraphs, originals))
                except ValueError:
                    if criterion['result'] != 'pending':
                        raise
            criterion['evidence'] = verified
            criterion['model_result'], criterion['model_reason'] = criterion['result'], criterion['reason']
            check_criterion(criterion, check, paragraphs)
            if criterion['result'] == 'pending':
                criterion.setdefault('pending_reason', 'applicability' if item['applicability'] == 'uncertain' else 'missing_evidence')
        except ValueError as exc:
            criterion = {**spec, 'result': 'pending', 'finding_type': 'processing_error',
                         'reason': '模型输出未完成核验：' + str(exc), 'evidence': [],
                         'pending_reason': 'processing_error', 'processing_error': str(exc)}
        cleaned.append(criterion)
    return summarize(item, cleaned, check['rule']['mode'] == 'presence')


def merge_repair(previous, repaired, presence=False):
    """A format repair cannot overwrite already verified sibling criteria."""
    if previous.get('status') != 'partial':
        return repaired
    if previous['applicability'] != repaired['applicability']:
        return {**previous, 'repair_error': '输出修复改变了规则适用性；保留已核验子项，适用性需独立复核。'}
    by_name = {c['criterion']: c for c in repaired['criteria']}
    criteria = [by_name.get(c['criterion'], c) if c.get('processing_error') else c for c in previous['criteria']]
    base = {**previous, 'reason': '；'.join(c['reason'] for c in criteria), 'suggestion': repaired.get('suggestion','')}
    base.pop('repair_error', None)
    return summarize(base, criteria, presence)

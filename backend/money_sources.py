"""Locate labelled monetary facts in the supplied report text, including table units."""
from decimal import Decimal, InvalidOperation
import re

NUMBER = r'\d+(?:,\d{3})*(?:\.\d+)?'
UNIT = r'亿元|万元|元|亿|万'
ALIASES = {'万':'万元', '亿':'亿元'}
SCALE = {'元':Decimal(1), '万元':Decimal(10000), '亿元':Decimal(100000000)}
LABEL = r'工程总投资|项目总投资|项目资金总额|总投资|资金总额|合\s*计|设计费|调试费|设备费|施工费|主材料费|主材费|材料费|建筑工程费|安装工程费|其他费用|预备费|建设管理费'
TAIL = r'(?P<value>'+NUMBER+r')(?![\d.年])\s*(?P<unit>'+UNIT+r')?'
LABELLED = re.compile(r'(?P<label>'+LABEL+r')(?:[（(][^）)]{0,20}[）)])?\s*[；;：:|]?\s*(?:(?P=label)\s*\|\s*)?'+TAIL)
DOUBLE_ROW = re.compile(r'(?P<label>[\u4e00-\u9fff][\u4e00-\u9fff、，（）()—\s]{0,30}?)\s*\|\s*(?P=label)\s*\|\s*'+TAIL)
HEADER = re.compile(r'(?:单位\s*[:：]?\s*|(?:费用|金额|总价)\s*[（(]\s*)(?P<unit>'+UNIT+r')[）)]?')
BLOCK = re.compile(r'序号\s*\||科目\s*\|')


def label_key(label):
    label = re.sub(r'\s+', '', label)
    if label in ('工程总投资','项目总投资','项目资金总额','总投资','资金总额','合计'):
        return '总投资'
    return {'主材料费':'主材费'}.get(label,label)


def expense_facts(paragraphs):
    """Each returned quote is an exact source span, with any inherited unit header."""
    facts=[]
    for pid,text in paragraphs.items():
        headers=list(HEADER.finditer(text))
        boundaries=[m.start() for m in BLOCK.finditer(text)]
        seen=set()
        for pattern in (DOUBLE_ROW, LABELLED):
            for match in pattern.finditer(text):
                label=label_key(match['label'])
                identity=match.start('value')
                if identity in seen:
                    continue
                seen.add(identity)
                # Read the complete unit before excluding prices such as 万元/台.
                if text[match.end():].lstrip().startswith(('/', '／')):
                    continue
                unit=match.groupdict().get('unit')
                begin=match.start()
                if not unit:
                    preceding=[h for h in headers if h.end() <= match.start()]
                    if not preceding:
                        continue
                    header=preceding[-1]
                    unit=header['unit'];begin=header.start()
                unit=ALIASES.get(unit,unit)
                facts.append(dict(paragraph_id=pid,quote=text[begin:match.end()],object=label,
                                  state='scope',value=match['value'].replace(',',''),unit=unit,
                                  amount_label=label,total_kind='subtotal' if re.sub(r'\s+','',match['label'])=='合计' else 'project',source_offset=match.start(),
                                  source_block=sum(b<=match.start() for b in boundaries)))
    return facts


def fact_value(fact):
    return Decimal(fact['value']) * SCALE[fact['unit']]


def resolve_expense_ref(ref, paragraphs, documents=None):
    """Repair only uniquely identified labels/amounts, never arbitrary quote text."""
    if not isinstance(ref,dict):
        return ref
    pid=ref.get('paragraph_id')
    if isinstance(pid,str) and pid.isdecimal():
        pid=int(pid)
    if pid not in paragraphs:
        return ref
    quote=ref.get('quote') or ''
    hints=[label_key(m.group()) for m in re.finditer(LABEL,quote)]
    obj=label_key(str(ref.get('object','')))
    # Table-specific labels (e.g. 主要设备/其他) are also present in the source catalog.
    facts=expense_facts(paragraphs)
    labels={f['amount_label'] for f in facts}
    if obj in labels:
        hints.append(obj)
    if not hints and '|' in quote:
        hints.append(label_key(quote.split('|')[0].strip()))
    hints=set(hints)
    value=ref.get('value')
    if value in (None,''):
        numbers=re.findall(NUMBER,quote)
        if len(numbers)==1:
            value=numbers[0]
    try:
        number=Decimal(str(value).replace(',',''))
    except (InvalidOperation, ValueError):
        return ref
    unit=ALIASES.get(ref.get('unit'),ref.get('unit'))
    candidates=[f for f in facts if f['amount_label'] in hints and Decimal(f['value'])==number and (not unit or unit==f['unit'])]
    local=[f for f in candidates if f['paragraph_id']==pid]
    if local:
        candidates=local
    else:
        # Corroboration in another paragraph is allowed only within a known document.
        candidates=[f for f in candidates if documents and pid in documents and documents.get(f['paragraph_id'])==documents[pid]]
    signatures={(f['amount_label'],fact_value(f)) for f in candidates}
    if len(signatures)!=1:
        return ref
    fact=candidates[0]
    return {**ref,**fact,'state':ref.get('state','scope'),'reference_mode':'source_amount'}


def expense_groups(check, paragraphs):
    """Recover coherent expense blocks, avoiding cross-document and subtotal sums."""
    docs=check.get('_paragraph_documents',{})
    cited={r['paragraph_id'] for r in check.get('_criterion_refs',[])}
    allowed_docs={docs[p] for p in cited if p in docs}
    usable={pid:text for pid,text in paragraphs.items()
            if not any(t in text for t in ('方案一','方案二','改造前投资','原工程总投资'))
            and (not cited or pid in cited or (allowed_docs and docs.get(pid) in allowed_docs))}
    groups={}
    facts=expense_facts(usable)
    for fact in facts:
        groups.setdefault((fact['paragraph_id'],fact['source_block']),[]).append(fact)
    candidates=[]
    for group in groups.values():
        text=usable[group[0]['paragraph_id']]
        # Nested costs need the explicit calculation; do not flatten parent/child fees.
        if re.search(r'(?:含|包括|其中)\s*(?:'+LABEL+r')',text):
            continue
        totals=[f for f in group if f['amount_label']=='总投资']
        expenses=[f for f in group if f['amount_label']!='总投资']
        if not totals or not expenses:
            continue
        by_label={}
        for fact in group:
            by_label.setdefault(fact['amount_label'],[]).append(fact)
        if any(len({fact_value(f) for f in fs})!=1 for fs in by_label.values()):
            continue
        pid=group[0]['paragraph_id']
        project_totals=[f for f in facts if f['amount_label']=='总投资' and f['total_kind']=='project'
                        and (f['paragraph_id']==pid or (pid in docs and docs.get(f['paragraph_id'])==docs[pid]))]
        if len({fact_value(f) for f in project_totals})>1:
            continue
        target=project_totals[0] if project_totals else totals[0]
        candidates.append([fs[0] for label,fs in by_label.items() if label!='总投资']+[target])
    # Repeated copies of the same estimate count once. Conflicting estimates are
    # not combined or silently selected as the project's authoritative total.
    unique={}
    for group in candidates:
        signature=tuple(sorted((f['amount_label'],fact_value(f)) for f in group))
        unique.setdefault(signature,group)
    return next(iter(unique.values())) if len(unique)==1 else None

"""Replay recorded model answers without API calls or changes to saved app runs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backend',type=Path,default=ROOT/'backend')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(args.backend.resolve()))
    from evidence_checks import EVIDENCE_POLICY, monetary_criterion
    from judgment_contract import validate
    rows=[]
    for folder in ('small-baseline-20260923','review-generalization-20260923'):
        for path in sorted((ROOT/'artifacts'/folder).glob('*/call-*.json')):
            record=json.loads(path.read_text(encoding='utf-8'))
            try:
                payload=json.loads(record['request']['messages'][-1]['content'])
                answer=json.loads(record['answer'].strip().removeprefix('```json').removesuffix('```').strip())
            except (ValueError,KeyError):
                continue
            rules={r['id']:r for r in payload.get('rules',[])}
            paragraphs={p['paragraph_id']:p['text'] for p in payload.get('paragraphs',[])}
            docs={p['paragraph_id']:p['document_id'] for p in payload.get('paragraphs',[]) if p.get('document_id') is not None}
            for item in (answer if isinstance(answer,list) else answer.get('judgments',[])):
                if not isinstance(item,dict):
                    continue
                rule=rules.get(item.get('rule_id'))
                if not rule or not any(monetary_criterion(n) for n in (rule.get('criteria') or [])):
                    continue
                check=dict(id=rule['id'],rule=rule,evidence_policy=EVIDENCE_POLICY,_paragraph_documents=docs)
                try:
                    out=validate(item,check,paragraphs)
                except ValueError:
                    continue
                for c in out['criteria']:
                    if monetary_criterion(c['criterion']):
                        rows.append(dict(source=str(path.relative_to(ROOT)),rule=rule['id'],criterion=c['criterion'],
                                         result=c['result'],processing_error=c.get('processing_error'),
                                         arithmetic=c.get('arithmetic'),reason=c['reason']))
    summary=dict(recorded_criterion_answers=len(rows),processing_errors=sum(bool(r['processing_error']) for r in rows),
                 arithmetic=dict(Counter(r['arithmetic']['status'] for r in rows if r['arithmetic'])),
                 note='Historical draft, audit and repair answers; repeated answers are not independent report samples.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(summary=summary,rows=rows),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()

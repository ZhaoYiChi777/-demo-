"""Small real-model acceptance examples; does not change projects or saved review results."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from main import model_gateway
from semantic_judgment import SemanticJudge

text='设备A设计压力为12MPa。设备B设计压力为8MPa。设备C设计压力尚未提供。二、改造的必要性：现有设备老化。三、方案论证：更换设备。'
evidence=[dict(id=1,filename='判定验收样例.txt',text=text)]
checks=[]
for rid in ('A','B','C'):
    rule=dict(id=rid,name=f'设备{rid}设计压力',category='special',mode='manual',standard=f'设备{rid}设计压力必须不低于10MPa。',content=f'核验设备{rid}设计压力是否达到10MPa。')
    checks.append(dict(id=rid,category='special',rule=rule,missing=[],evidence=evidence,retrieval=dict(status='complete')))
rule=dict(id='fields',name='章节标识',category='common',mode='presence',standard='包含建设必要性及技术方案',content='建设必要性及技术方案章节标识')
checks.append(dict(id='fields',category='common',rule=rule,missing=['建设必要性','技术方案'],evidence=evidence,retrieval=dict(status='complete')))
results,status=SemanticJudge(model_gateway).evaluate({'scope_name':'验收样例','brief':'','sources':[]},checks)
out=ROOT/'artifacts/semantic-judgment-validation';out.mkdir(parents=True,exist_ok=True)
(out/'controlled-live.json').write_text(json.dumps(dict(results=results,status=status),ensure_ascii=False,indent=2),encoding='utf-8')
actual={k:r['result'] for k,r in results.items()}
print(json.dumps(actual,ensure_ascii=False))
assert actual==dict(A='pass',B='fail',C='pending',fields='pass'),actual
print('PASS: real model supports compliance, contradiction, insufficient evidence and equivalent headings')

"""Use the configured live model to recover an evidence phrase with no literal hit."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import main

material={'scope_name':'语义检索联调样例','brief':'识别项目建设主体', 'chunks':[
    {'id':1,'filename':'测试报告.txt','text':'本工程由华东能源有限公司负责建设与实施。', 'page':1,'section_path':'项目说明'}]}
rules=[{'id':'TEST-ENTITY','name':'建设单位识别','terms':['建设单位'],'standard':'识别承担本项目建设工作的单位','content':'找到建设主体的原文表述'}]
found,status=main.workbench.retriever.retrieve(material,rules)
assert status['status']=='complete',status
result=found['TEST-ENTITY']
assert result['retrieval']['field_matches']==0,result
assert result['retrieval']['semantic_matches']>0,result
print('PASS: live semantic retrieval recovered evidence with zero literal hits')
print(result['evidence'][0]['semantic_quotes'][0]['quote'])

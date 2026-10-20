import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('LLM_RETRIEVAL_ENABLED', 'false')
from docx import Document
from fastapi.testclient import TestClient
from hybrid_retrieval import HybridRetriever
from semantic_judgment import SemanticJudge
import main


def check(mode='manual', missing=None, category='special'):
    return dict(id='r', category=category, missing=missing or [], rule=dict(id='r',mode=mode,category=category,
        name='方案检查',standard='方案需说明停机与调试',content='说明停机与调试'),
        evidence=[dict(id=1,text='随机组停机施工，完工后调试。')], retrieval=dict(status='complete'))


def decision(result='pass', criterion='停机与调试', quote='随机组停机施工，完工后调试。'):
    return dict(rule_id='r',applicability='applicable',coverage_complete=True,reason='原文明确说明施工和调试安排。',
        suggestion='保留依据',criteria=[dict(criterion=criterion,result=result,reason='与判据对应。',
            evidence=[dict(paragraph_id=1,quote=quote)])])


def judge_response(item):
    gateway=Mock()
    gateway.config.return_value=dict(enabled=True,key='test',model='test')
    gateway.json.return_value={'judgments':[item]}
    return SemanticJudge(gateway)


class JudgmentTests(unittest.TestCase):
    def test_normalized_definite_finding_also_requires_conclusion_audit(self):
        item = decision('pending')
        item['criteria'][0]['finding_type'] = 'supported'
        judge = judge_response(item)
        judge.gateway.json.side_effect = [{'judgments':[item]}, TimeoutError()]
        found, _ = judge.evaluate({}, [check()])
        self.assertEqual(judge.gateway.json.call_count, 2)
        self.assertEqual(found['r']['result'], 'pending')
        self.assertEqual(found['r']['status'], 'unavailable')

    def test_equivalent_field_and_professional_positive_and_negative(self):
        for mode, missing, name, verdict in [('presence',['技术方案'],'技术方案','pass'),('manual',[], '停机与调试','pass'),('manual',[],'停机与调试','fail')]:
            with self.subTest(mode=mode,verdict=verdict):
                found,status=judge_response(decision(verdict,name)).evaluate({},[check(mode,missing)])
                self.assertEqual(found['r']['result'],verdict)
                self.assertEqual(status['evaluated_checks'],1)

    def test_missing_field_coverage_and_invalid_quote_cannot_pass(self):
        for item in [decision(criterion='不同字段'),decision(criterion='技术方案',quote='原文不存在')]:
            found,status=judge_response(item).evaluate({},[check('presence',['技术方案'])])
            self.assertEqual(found['r']['result'],'pending')
            self.assertEqual(status['rejected_checks'],1)

    def test_fixed_field_coverage_is_derived_from_all_missing_fields(self):
        item={**decision(criterion='技术方案'),'coverage_complete':False}
        found,_=judge_response(item).evaluate({},[check('presence',['技术方案'])])
        self.assertEqual(found['r']['result'],'pass')
        found,_=judge_response(decision(criterion='技术方案章节')).evaluate({},[check('presence',['技术方案'])])
        self.assertEqual(found['r']['result'],'pass')

    def test_unsupported_criteria_and_inapplicable_and_incomplete_coverage_pending(self):
        for change in [dict(coverage_complete=False),dict(criteria=[dict(criterion='试验',result='pending',reason='未提供试验数据',evidence=[])])]:
            found,_=judge_response({**decision(),**change}).evaluate({},[check()])
            self.assertEqual(found['r']['result'],'pending')

    def test_empty_brief_and_no_evidence_do_not_call_model(self):
        for c in [check(category='topic'),{**check(),'evidence':[]}]:
            judge=judge_response(decision())
            found,status=judge.evaluate({'brief':''},[c])
            self.assertEqual(found['r']['result'],'pending')
            self.assertEqual(status['status'],'not_needed')
            judge.gateway.json.assert_not_called()

    def test_service_failure_and_incomplete_retrieval_remain_pending(self):
        judge=judge_response(decision());judge.gateway.json.side_effect=TimeoutError()
        found,status=judge.evaluate({},[check()])
        self.assertEqual(found['r']['result'],'pending')
        self.assertEqual(status['batches'][0]['error'],'TimeoutError')
        found,_=judge_response(decision()).evaluate({},[{**check(),'retrieval':{'status':'partial'}}])
        self.assertEqual(found['r']['result'],'pending')

    def test_scope_evidence_can_exclude_unrelated_equipment_without_keyword_hits(self):
        quote='本次仅优化1号机抽汽逆止门控制装置。'
        item={**decision(quote=quote),'applicability':'not_applicable'}
        judge=judge_response(item)
        c={**check(),'evidence':[]}
        material={'chunks':[dict(id=1,text=quote,section_path='项目范围')]}
        found,status=judge.evaluate(material,[c])
        self.assertEqual(found['r']['result'],'not_applicable')
        self.assertEqual(status['evaluated_checks'],1)
        self.assertEqual(judge.gateway.json.call_args.args[1]['paragraphs'][0]['text'],quote)

    def test_conclusion_audit_can_downgrade_and_failure_never_accepts_draft(self):
        for second in [{'judgments':[decision('pending')]}, TimeoutError()]:
            judge=judge_response(decision())
            judge.gateway.json.side_effect=[{'judgments':[decision()]},second]
            found,_=judge.evaluate({},[check()])
            self.assertEqual(found['r']['result'],'pending')
            self.assertEqual(judge.gateway.json.call_count,2)

    def test_pending_bad_optional_quote_is_removed(self):
        judge=judge_response(decision('pending',quote='并不存在的原句'))
        found,_=judge.evaluate({},[check()])
        self.assertEqual(found['r']['criteria'][0]['evidence'],[])
        self.assertEqual(found['r']['result'],'pending')

    def test_invalid_quote_repaired_once_without_changing_fail_verdict(self):
        valid=decision('fail')
        invalid=decision('fail',quote='不存在的引用')
        judge=judge_response(valid)
        judge.gateway.json.side_effect=[{'judgments':[valid]},{'judgments':[invalid]},{'judgments':[valid]}]
        found,_=judge.evaluate({},[check()])
        self.assertEqual(found['r']['result'],'fail')
        self.assertTrue(found['r']['output_repaired'])
        self.assertEqual(judge.gateway.json.call_count,3)

    def test_invalid_json_retries_once_then_still_requires_valid_evidence(self):
        judge=judge_response(decision())
        judge.gateway.json.side_effect=[json.JSONDecodeError('Invalid','',0),{'judgments':[decision()]},{'judgments':[decision()]}]
        found,_=judge.evaluate({},[check()])
        self.assertEqual(found['r']['result'],'pass')
        self.assertEqual(judge.gateway.json.call_count,3)

    def test_all_entry_points_persist_decisions_and_export_quotes(self):
        calls=[]
        def response(prompt,payload):
            if 'judgments' in prompt:
                calls.append('judge')
                rows=[]
                paragraphs={p['paragraph_id']:p['text'] for p in payload['paragraphs']}
                for r in payload['rules']:
                    pid=r['evidence_ids'][0]
                    names=r['missing'] if r['mode']=='presence' else r.get('criteria') or ['按规则核验安排']
                    rows.append(dict(rule_id=r['id'],applicability='applicable',coverage_complete=True,reason='测试：原文满足判据。',suggestion='保留材料',
                        criteria=[dict(criterion=n,result='pass',reason='测试证据支持。',evidence=[dict(paragraph_id=pid,quote=paragraphs[pid][:20])]) for n in names]))
                return {'judgments':rows}
            p=payload['paragraphs'][0]
            return {'matches':[dict(rule_id=r['id'],evidence=[dict(paragraph_id=p['paragraph_id'],quote=p['text'][:20],reason='测试关联')]) for r in payload['rules']]}
        gateway=Mock();gateway.config.return_value=dict(enabled=True,key='test',model='test');gateway.json.side_effect=response
        with tempfile.TemporaryDirectory() as temp, patch.object(main,'DB_PATH',Path(temp)/'test.db'), patch.object(main.workbench,'retriever',HybridRetriever(gateway)):
            with TestClient(main.app) as client:
                from test_accounts_support import seed_demo
                initial=seed_demo(client)['run']
                single=client.post('/api/workbench/projects/DEMO-REVIEW/runs',json={}).json()
                self.assertEqual(single['counts']['pass'],7)
                self.assertTrue(all(c['judgment']['status']=='completed' for c in single['checks'] if c.get('judgment')))
                self.assertEqual(client.get('/api/workbench/runs/'+single['id']).json()['judgment'],single['judgment'])
                doc=Document(io.BytesIO(client.get('/api/workbench/runs/'+single['id']+'/report.docx').content))
                self.assertIn('判定引用','\n'.join(p.text for p in doc.paragraphs))
                batch=client.post('/api/workbench/batches',json={'name':'判定复用','items':[{'name':'甲','filename':'甲.txt','brief':'任务目标'}]}).json()
                pid=batch['items'][0]['item_id'];material=main.workbench.materials('DEMO-REVIEW')
                with patch.object(main.workbench,'materials',return_value=material):
                    main.batch_reviews.update(pid,'ready',uploaded=True)
                    batch_run=client.post(f"/api/workbench/batches/{batch['id']}/items/{pid}/run").json()
                self.assertEqual(batch_run['judgment']['status'],'complete')
                rounds=[[{'type':'response.output_item.done','item':dict(type='function_call',name='run_review',arguments='{}',call_id='run')}],[{'type':'response.output_text.delta','delta':'评审已完成'}]]
                with patch.object(main.review_agent,'config',return_value=dict(enabled=True,key='test')),patch.object(main.review_agent,'model_events',side_effect=rounds):
                    reply=client.post('/api/workbench/projects/DEMO-REVIEW/agent/chat',json={'message':'请重新评审','run_id':single['id']})
                events=[json.loads(line) for line in reply.text.splitlines()]
                self.assertTrue(any(e['type']=='run' for e in events),events)
                latest=client.get('/api/workbench/projects/DEMO-REVIEW/runs/latest').json()
                self.assertEqual(latest['judgment']['status'],'complete')
                self.assertGreaterEqual(len(calls),4)
                self.assertEqual(client.get('/api/workbench/runs/'+initial['id']).json(),initial)


if __name__=='__main__':unittest.main()

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
os.environ.setdefault('LLM_RETRIEVAL_ENABLED','false')
from fastapi.testclient import TestClient
import main
from semantic_judgment import validate
from rule_contracts import contracted
from fsr_rubric import POLICY, scoring_rules, select_branch
from quality_scoring import quality_score
from hybrid_retrieval import HybridRetriever


def rubric_run(policy='fsr-70-30-v3'):
    branch={'id':'safety','name':'安全可靠性'}
    checks=[]
    for rule in scoring_rules(branch):
        checks.append(dict(id=rule['id'],rule=rule,category=rule['category'],result='pass',reason='证据已核验',
            judgment=dict(status='completed',result='pass',applicability='applicable',criteria=[
                dict(criterion=n,result='pass',reason='有证据',evidence=[]) for n in rule['criteria']])))
    return dict(scoring_policy=policy,scoring_branch=branch,checks=checks)


class RubricTests(unittest.TestCase):
    def test_v4_hard_maximum_preserves_raw_points_and_lower_scores(self):
        run=rubric_run(POLICY)
        q=quality_score(run)
        self.assertEqual(q['raw_total'],100)
        self.assertEqual((q['total'],q['upper_bound'],q['maximum']),(92,92,92))
        self.assertEqual(q['caps'][0]['limit'],92)
        for c in run['checks'][:2]:
            for criterion in c['judgment']['criteria']:criterion['result']='pending'
        q=quality_score(run)
        self.assertEqual(q['total'],80);self.assertEqual(q['upper_bound'],92)
        self.assertIsNone(q['grade'])
        next(c for c in run['checks'] if c['id']=='FSR-CAP49')['result']='fail'
        self.assertEqual(quality_score(run)['total'],49)
        run['scoring_policy']='fsr-70-30-v3'
        next(c for c in run['checks'] if c['id']=='FSR-CAP49')['result']='pass'
        self.assertEqual(quality_score(run)['upper_bound'],100)

    def test_fixed_positive_contract_and_no_scope_expansion(self):
        for rid in ('TUR-NEC-004','TUR-DOC-003'):
            rule=contracted(dict(id=rid,mode='manual'))
            c=dict(id=rid,rule=rule,missing=[])
            item=dict(applicability='applicable',coverage_complete=True,reason='原文证明正向条件成立',criteria=[
                dict(criterion=n,result='pass',reason='原文支持',evidence=[dict(paragraph_id=1,quote='本厂多次故障；投资估算总投资90万元。')]) for n in rule['criteria']])
            self.assertEqual(validate(item,c,{1:'本厂多次故障；投资估算总投资90万元。'})['result'],'pass')
            item['criteria'].append(dict(criterion='设计费不得为无',result='fail',reason='模型扩展要求',evidence=[dict(paragraph_id=1,quote='90万元')]))
            result=validate(item,c,{1:'本厂多次故障；投资估算总投资90万元。'})
            self.assertEqual(result['result'],'pass')
            self.assertEqual([k['criterion'] for k in result['criteria']],rule['criteria'])

    def test_weighted_partial_credit_zero_pending_and_caps(self):
        run=rubric_run();q=quality_score(run)
        self.assertEqual(q['total'],100);self.assertEqual(q['grade'],'A')
        run['checks'][0]['judgment']['criteria'][0]['result']='pending'
        q=quality_score(run)
        self.assertEqual(q['total'],97);self.assertEqual(q['upper_bound'],100)
        self.assertEqual(q['coverage'],97);self.assertIsNone(q['grade'])
        run['checks'][0]['judgment']['criteria'][0]['result']='pass'
        next(c for c in run['checks'] if c['id']=='FSR-CAP49')['result']='fail'
        self.assertEqual(quality_score(run)['total'],49)
        self.assertEqual(quality_score(run)['grade'],'D')

    def test_unresolved_and_inapplicable_do_not_earn_half_points(self):
        run=rubric_run()
        for c in run['checks']:
            c['result']='pending';c['judgment']={'status':'unavailable','criteria':[]}
        self.assertIsNone(quality_score(run)['total'])
        self.assertEqual(quality_score(run)['coverage'],0)
        run['judgment']={'status':'unavailable'}
        self.assertIsNone(quality_score(run)['total'])
        self.assertEqual(quality_score(run)['scoring_status'],'processing_failed')
        run=rubric_run();c=next(c for c in run['checks'] if c['id']=='FSR-B1')
        c['result']='not_applicable'
        q=quality_score(run);self.assertEqual(q['total'],100)
        self.assertTrue(next(d for d in q['dimensions'] if d['id']=='B1')['excluded'])
        self.assertAlmostEqual(sum(d['effective_weight'] for d in q['dimensions'] if d['group']=='branch'),30)

    def test_branch_selection_uses_report_not_other_project_reports(self):
        expected=[('抗燃油系统改造','safety'),('空冷岛综合治理','energy'),('工业抽汽新增高压汽源','heat'),('汽轮机通流改造','turbine'),('低压缸微出力科技创新篇','innovation')]
        for title,key in expected:self.assertEqual(select_branch({'scope_name':title,'sources':[]})['id'],key)
        self.assertIsNone(select_branch({'scope_name':'待识别','sources':[]})['id'])

    def test_old_record_keeps_its_snapshot_criteria_and_policy(self):
        run=rubric_run()
        run['scoring_policy']='fsr-70-30-v1'
        c=run['checks'][0]
        c['rule']['criteria'][2]='证据标明时间、对象和测量或记录来源'
        c['judgment']['criteria'][2].update(criterion=c['rule']['criteria'][2],result='pending')
        q=quality_score(run)
        self.assertEqual(q['policy'],'fsr-70-30-v1')
        self.assertEqual(q['total'],97)
        self.assertEqual(q['dimensions'][0]['pending'],1)

    def test_legacy_revision_keeps_hard_cap_and_user_edited_rules(self):
        run=rubric_run()
        run['scoring_policy']='fsr-70-30-v2'
        next(c for c in run['checks'] if c['id']=='FSR-CAP69')['result']='fail'
        self.assertEqual(quality_score(run)['total'],69)
        custom=dict(id='TUR-SCP-001',origin='custom',version=7,standard='必须提供逐机设备明细')
        self.assertEqual(contracted(custom),custom)

    def test_rejected_cap_cannot_publish_an_uncapped_score(self):
        run=rubric_run()
        run['scoring_policy']='fsr-70-30-v2'
        c=next(c for c in run['checks'] if c['id']=='FSR-CAP69')
        c.update(result='pending',judgment={'status':'rejected','criteria':[]})
        self.assertIsNone(quality_score(run)['total'])
        self.assertEqual(quality_score(run)['scoring_status'],'processing_failed')

    def test_v3_investment_conflict_deducts_items_without_69_cap(self):
        run=rubric_run()
        next(c for c in run['checks'] if c['id']=='FSR-CAP69')['result']='fail'
        for rid,index,result in [('FSR-C7',0,'fail'),('FSR-C8',2,'fail'),('FSR-B2',1,'pending')]:
            next(c for c in run['checks'] if c['id']==rid)['judgment']['criteria'][index]['result']=result
        q=quality_score(run)
        self.assertEqual(q['total'],93.5);self.assertEqual(q['upper_bound'],97.5)
        self.assertEqual(q['caps'],[]);self.assertTrue(q['decision_notes'])
        self.assertTrue(q['provisional']);self.assertIsNone(q['grade'])
        run['scoring_policy']='fsr-70-30-v2'
        old=quality_score(run)
        self.assertEqual(old['total'],69);self.assertEqual(old['upper_bound'],69)

    def test_v3_consistency_check_failure_retains_score_with_pending_notice(self):
        run=rubric_run()
        c=next(c for c in run['checks'] if c['id']=='FSR-CAP69')
        c.update(result='pending',reason='核验未完成',judgment={'status':'rejected','criteria':[]})
        q=quality_score(run)
        self.assertEqual(q['total'],100);self.assertTrue(q['provisional'])
        self.assertIsNone(q['grade']);self.assertEqual(q['decision_notes'][0]['result'],'pending')
        next(c for c in run['checks'] if c['id']=='FSR-CAP49').update(result='pending',judgment={'status':'unavailable'})
        self.assertIsNone(quality_score(run)['total'])


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=patch.object(main,'DB_PATH',Path(self.temp.name)/'review.db');self.db.start()
        self.client=TestClient(main.app);self.client.__enter__()
        from test_accounts_support import seed_demo
        self.run=seed_demo(self.client)['run']
        self.gateway=Mock();self.gateway.config.return_value=dict(enabled=True,key='test',model='test')
        self.gateway.json.side_effect=self.response
        self.retriever=patch.object(main.workbench,'retriever',HybridRetriever(self.gateway));self.retriever.start()
        self.path='/api/workbench/projects/DEMO-REVIEW/agent'
        self.invalid=False;self.disagree=False;self.judge_calls=0

    def tearDown(self):
        self.retriever.stop();self.client.__exit__(None,None,None);self.db.stop();self.temp.cleanup()

    def response(self,prompt,payload):
        p=payload['paragraphs'][0]
        if 'judgments' not in prompt:
            return {'matches':[dict(rule_id=r['id'],evidence=[dict(paragraph_id=p['paragraph_id'],quote=p['text'][:30],reason='相关原文')]) for r in payload['rules']]}
        self.judge_calls+=1
        result='pending' if self.disagree and self.judge_calls%2==0 else 'pass'
        return {'judgments':[dict(rule_id=r['id'],applicability='applicable',coverage_complete=True,reason='逐项核验有原文支持。',suggestion='保留核验依据',criteria=[
            dict(criterion=n,result=result,reason='原文支持',evidence=[dict(paragraph_id=p['paragraph_id'],quote='虚构引用' if self.invalid else p['text'][:30])])
            for n in (r.get('criteria') or r['missing'] or ['明确措施'])]) for r in payload['rules']]}

    def inspect(self):
        response=self.client.post(self.path+'/audit',json={'run_id':self.run['id'],'check_ids':['DEMO-004','DEMO-007']})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['proposal']

    def test_selective_application_history_score_and_no_report_qualification(self):
        proposal=self.inspect()
        self.assertEqual(sum(r['eligible'] for r in proposal['rows']),2)
        before=self.client.get('/api/workbench/runs/'+self.run['id']).json()
        self.assertEqual(before,self.run)
        response=self.client.post(self.path+'/audit/apply',json={'proposal_id':proposal['id'],'check_ids':['DEMO-004']})
        self.assertEqual(response.status_code,200,response.text)
        updated=response.json()['run'];a=next(c for c in updated['checks'] if c['id']=='DEMO-004');b=next(c for c in updated['checks'] if c['id']=='DEMO-007')
        self.assertEqual(a['result'],'pass');self.assertEqual(b['result'],'pending')
        self.assertEqual(a['history'][-1]['result'],'pending')
        self.assertEqual(updated['status'],'completed');self.assertNotIn('qualification',updated)
        self.assertEqual(updated['counts']['pass'],self.run['counts']['pass']+1)
        logs=self.client.get(self.path+'/logs').json()['logs']
        self.assertEqual(len(logs),2);self.assertTrue(all(l['finished_at'] for l in logs))

    def test_batch_policy_reaches_both_independent_judgments_without_changing_rules(self):
        from audit_policy import AUDIT_POLICY, AUDIT_PROMPT
        from semantic_judgment import SemanticJudge
        proposal=self.inspect()
        self.assertEqual(proposal['policy'],AUDIT_POLICY)
        judge_calls=[call.args for call in self.gateway.json.call_args_list if 'judgments' in call.args[0]]
        self.assertGreaterEqual(len(judge_calls),2)
        for prompt,payload in judge_calls:
            self.assertIn(AUDIT_PROMPT,prompt)
            self.assertNotIn('draft_judgments',payload)
            self.assertEqual(len(payload['rules']),1)
            for rule in payload['rules']:
                original=next(c['rule'] for c in self.run['checks'] if c['id']==rule['id'])
                expected=contracted(original)
                self.assertEqual(rule['criteria'],expected.get('terms',[]) if expected['mode']=='presence' else expected.get('criteria'))
        self.assertEqual(self.client.get('/api/workbench/runs/'+self.run['id']).json(),self.run)
        # Ordinary automatic reviews must not silently inherit the batch-only instructions.
        self.gateway.reset_mock()
        material=main.workbench.materials('DEMO-REVIEW',None)
        checks=[c for c in self.run['checks'] if c['id']=='DEMO-004']
        SemanticJudge(self.gateway).evaluate(material,checks)
        ordinary=[c.args[0] for c in self.gateway.json.call_args_list if 'judgments' in c.args[0]]
        self.assertTrue(ordinary)
        self.assertTrue(all(AUDIT_PROMPT not in prompt for prompt in ordinary))

    def test_invalid_citation_or_disagreement_cannot_batch_pass(self):
        for invalid,disagree in [(True,False),(False,True)]:
            self.invalid=invalid;self.disagree=disagree;self.judge_calls=0
            proposal=self.inspect();self.assertFalse(any(r['eligible'] for r in proposal['rows']))
            if invalid:
                self.assertIn('不能据此认定报告缺证',proposal['summary'])
                self.assertNotIn('恢复服务',proposal['summary'])
            response=self.client.post(self.path+'/audit/apply',json={'proposal_id':proposal['id'],'check_ids':['DEMO-004']})
            self.assertEqual(response.status_code,422)
            self.assertEqual(self.client.get('/api/workbench/runs/'+self.run['id']).json(),self.run)

    def test_updated_record_rejects_whole_proposal_without_partial_writes(self):
        proposal=self.inspect()
        self.client.patch('/api/workbench/runs/'+self.run['id']+'/checks/DEMO-001',json={'result':'pending','reason':'人工复核中','suggestion':'稍后确认'})
        response=self.client.post(self.path+'/audit/apply',json={'proposal_id':proposal['id'],'check_ids':['DEMO-004','DEMO-007']})
        self.assertEqual(response.status_code,409)
        current=self.client.get('/api/workbench/runs/'+self.run['id']).json()
        self.assertTrue(all(c['result']=='pending' for c in current['checks'] if c['id'] in ['DEMO-004','DEMO-007']))

    def test_provider_balance_failure_is_logged_as_error(self):
        self.gateway.json.side_effect=HTTPError('model',402,'Insufficient Balance',{},None)
        proposal=self.inspect()
        self.assertFalse(any(r['eligible'] for r in proposal['rows']))
        self.assertEqual(self.client.get(self.path+'/logs').json()['logs'][0]['status'],'error')
        self.assertIn('恢复服务',proposal['summary'])


if __name__=='__main__':unittest.main()

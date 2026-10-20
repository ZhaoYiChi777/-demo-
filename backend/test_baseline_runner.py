from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_model_baselines import DirectJudge, FullTextRetriever, judge_factory, SemanticJudge
from evidence_checks import EVIDENCE_POLICY


class BaselineRunnerTests(unittest.TestCase):
    def sample(self):
        chunks=[dict(id=1,document_id=1,text='设备费100万元，总投资100万元。',filename='sample.txt',section_path='投资',page=1)]
        material=dict(scope_name='测试',brief='',sources=[dict(document_category='project_report',parse_status='parsed')],chunks=chunks)
        rule=dict(id='FSR-C7',name='费用',category='common',mode='manual',contract_version=4,
                  criteria=['工程总投资与费用分项完整且闭合'],standard='核验费用',content='核验费用',terms=[])
        check=dict(id=rule['id'],rule=rule,category='common',missing=[],evidence=deepcopy(chunks),retrieval=dict(status='complete',coverage_policy='per-rule-window-v1'))
        return material,check

    def test_direct_receives_full_text_and_uses_same_money_validation_once(self):
        material,check=self.sample();gateway=Mock();gateway.config.return_value=dict(enabled=True,key='offline',model='offline')
        def respond(prompt,payload):
            self.assertEqual(payload['paragraphs'][0]['text'],material['chunks'][0]['text'])
            self.assertNotIn('draft_judgments',payload)
            return dict(judgments=[dict(rule_id='FSR-C7',applicability='applicable',coverage_complete=True,reason='金额明确',criteria=[dict(
                criterion_id='FSR-C7:1',result='pass',reason='费用闭合',evidence=[dict(paragraph_id=1)],calculation=dict(operation='sum',complete=True))])])
        gateway.json.side_effect=respond
        with patch('semantic_judgment.HybridRetriever',side_effect=AssertionError('Direct method must not retrieve')):
            output,status=DirectJudge(gateway).evaluate(material,[check])
        self.assertEqual(gateway.json.call_count,1)
        self.assertEqual(output['FSR-C7']['criteria'][0]['arithmetic']['status'],'matched')
        self.assertEqual(status['status'],'complete')

    def test_direct_does_not_repair_missing_criteria_or_hide_failure(self):
        material,check=self.sample();gateway=Mock();gateway.config.return_value=dict(enabled=True,key='offline',model='offline')
        gateway.json.return_value=dict(judgments=[dict(rule_id='FSR-C7',applicability='applicable',reason='未答',criteria=[])])
        output,status=DirectJudge(gateway).evaluate(material,[check])
        self.assertEqual(gateway.json.call_count,1)
        self.assertEqual(output['FSR-C7']['status'],'rejected')
        self.assertEqual(status['rejected_checks'],1)

    def test_method_binding_is_per_gateway(self):
        a=Mock();a.method='direct';b=Mock();b.method='system'
        self.assertIsInstance(judge_factory(a,lambda:True),DirectJudge)
        self.assertIsInstance(judge_factory(b,lambda:True),SemanticJudge)
        material,check=self.sample()
        found,status=FullTextRetriever(a).retrieve(material,[check['rule']])
        self.assertEqual(found['FSR-C7']['evidence'],material['chunks'])
        self.assertEqual(status['total_batches'],0)


if __name__=='__main__':unittest.main()

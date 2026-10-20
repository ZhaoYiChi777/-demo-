import csv
from pathlib import Path
import unittest
from atomic_contracts import FIXED, source_metadata
from rule_contracts import contracted
from review_conclusion import project_conclusion
from semantic_judgment import SemanticJudge, validate
from unittest.mock import Mock


def check(level, result='fail', status='completed', applicability='applicable'):
    return dict(id='example',rule=dict(name='原规则',source_metadata={'severity':level}),
                result=result,reason='核验依据',judgment=dict(status=status,applicability=applicability))


class AtomicContractTests(unittest.TestCase):
    def test_all_original_rules_have_fixed_criteria_and_original_levels(self):
        with (Path(__file__).resolve().parents[1]/'csv/汽机项目预评审完整知识库_v1__原子规则库.csv').open(encoding='utf-8-sig') as file:
            for _ in range(3): next(file)
            rows=list(csv.DictReader(file))
        self.assertEqual({r['规则ID'] for r in rows},set(FIXED))
        self.assertEqual(len(FIXED),80)
        counts={i:0 for i in range(4)}
        for row in rows:
            metadata=source_metadata(row); counts[metadata['severity']]+=1
            rule=contracted(dict(id=row['规则ID'],origin='default',standard=row['触发条件（自然语言）'],source_metadata=metadata))
            self.assertTrue(rule['criteria']);self.assertTrue(rule['applicability_condition'])
            self.assertEqual(rule['source_metadata']['system_scope'],row['适用系统/技术方向'])
            self.assertEqual(rule['source_metadata']['conclusion_impact'],row['总体结论影响'])
        self.assertEqual(counts,{0:11,1:41,2:24,3:4})

    def test_client_four_levels_and_hard_does_not_mean_veto(self):
        self.assertEqual(project_conclusion([check(0,'pass')])['label'],'一致通过')
        self.assertEqual(project_conclusion([check(1)])['label'],'少量修改后通过')
        self.assertEqual(project_conclusion([check(1),check(2)])['label'],'大量修改后通过')
        self.assertEqual(project_conclusion([check(3)])['label'],'不通过')

    def test_pending_veto_or_model_error_cannot_become_rejection_or_approval(self):
        for c in (check(3,'pending'),check(2,'pending','unavailable'),check(2,'pending',applicability='uncertain')):
            opinion=project_conclusion([check(0,'pass'),c])
            self.assertIsNone(opinion['level']);self.assertTrue(opinion['provisional'])
        opinion=project_conclusion([check(1,'pending')])
        self.assertEqual(opinion['level'],1);self.assertTrue(opinion['provisional'])
        self.assertEqual(project_conclusion([check(0,'pending'),check(1,'pass')])['level'],0)

    def test_custom_edits_and_fixed_criteria_validation(self):
        custom=dict(id='TUR-TEC-002',origin='custom',standard='自定义规则')
        self.assertEqual(contracted(custom),custom)
        rule=contracted(dict(id='TUR-INV-003',mode='manual'))
        self.assertNotIn('总投资',rule['standard'])
        item=dict(applicability='applicable',coverage_complete=True,reason='证据',criteria=[dict(criterion='额外要求全部金额可确认',result='pass',reason='证据',evidence=[dict(paragraph_id=1,quote='设备费120，主材0')])])
        result=validate(item,dict(rule=rule,id=rule['id']),{1:'设备费120，主材0'})
        self.assertEqual(result['status'],'rejected')
        self.assertTrue(all(c['result']=='pending' for c in result['criteria']))
        self.assertEqual([c['criterion'] for c in result['criteria']],rule['criteria'])

    def test_file_readability_uses_parser_not_body_file_format_words(self):
        rule=contracted(dict(id='TUR-DOC-005',mode='manual'))
        gateway=Mock();gateway.config.return_value={'enabled':False,'key':''}
        material=dict(sources=[dict(document_category='project_report',parse_status='parsed')],chunks=[dict(id=1,text='项目建设必要性及方案正文')])
        results,_=SemanticJudge(gateway).evaluate(material,[dict(id=rule['id'],rule=rule,category='common',missing=[],evidence=[])])
        self.assertEqual(results[rule['id']]['result'],'pass');gateway.json.assert_not_called()

    def test_missing_design_is_pending_even_when_model_calls_it_failure(self):
        rule=contracted(dict(id='TUR-TEC-002',mode='manual'))
        item=dict(applicability='applicable',coverage_complete=False,reason='缺设计说明',criteria=[dict(criterion=rule['criteria'][0],result='fail',finding_type='missing_evidence',reason='未说明新方案失电措施',evidence=[])])
        self.assertEqual(validate(item,dict(rule=rule,id=rule['id']),{})['result'],'pending')

    def test_single_submitted_version_is_not_a_missing_version(self):
        rule=contracted(dict(id='TUR-CON-001',mode='manual'))
        gateway=Mock();gateway.config.return_value={'enabled':False,'key':''}
        material=dict(sources=[dict(document_category='project_report',parse_status='parsed')],chunks=[dict(id=1,text='当前项目报告正文')])
        results,_=SemanticJudge(gateway).evaluate(material,[dict(id=rule['id'],rule=rule,category='special',missing=[],evidence=[])])
        self.assertEqual(results[rule['id']]['result'],'not_applicable');gateway.json.assert_not_called()


if __name__=='__main__':unittest.main()

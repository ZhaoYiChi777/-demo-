import json
from copy import deepcopy
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

from judgment_contract import validate, source_ref, merge_repair
from semantic_judgment import SemanticJudge
from quality_scoring import quality_score
from fsr_rubric import POLICY, scoring_rules
from review_conclusion import project_conclusion
from evidence_checks import EVIDENCE_POLICY


TEXT = '本次仅更换冷却泵。\n 计划停机施工，完工后调试。'


def check():
    return dict(id='custom-rule',category='special',missing=[],
                rule=dict(id='custom-rule',mode='manual',origin='custom',criteria=['范围明确','施工和调试明确']),
                evidence=[dict(id=1,text=TEXT)],retrieval={'status':'complete'})


def decision():
    return dict(rule_id='custom-rule',applicability='applicable',coverage_complete=True,reason='正文说明范围与安排',criteria=[
        dict(criterion_id='custom-rule:1',result='pass',reason='设备范围明确',evidence=[{'paragraph_id':'1'}]),
        dict(criterion_id='custom-rule:2',result='pass',reason='有停机及调试安排',evidence=[{'paragraph_id':1,'quote':'原文未出现的安排'}])])


def rubric_run():
    branch={'id':'safety','name':'安全可靠性'}
    rows=[]
    for rule in scoring_rules(branch):
        rows.append(dict(id=rule['id'],rule=rule,category=rule['category'],result='pass',reason='完成',judgment=dict(
            status='completed',applicability='applicable',criteria=[dict(criterion=n,result='pass',reason='原文支持',evidence=[]) for n in rule['criteria']])))
    return dict(scoring_policy=POLICY,scoring_branch=branch,checks=rows,retrieval={'coverage_policy':'per-rule-window-v1'})


class ContractTests(unittest.TestCase):
    def test_ids_and_paragraph_references_retain_exact_original(self):
        result=validate(decision(),check(),{1:TEXT})
        self.assertEqual(result['status'],'partial')
        self.assertEqual([c['result'] for c in result['criteria']],['pass','pending'])
        self.assertEqual(result['criteria'][0]['criterion'],'范围明确')
        self.assertEqual(result['criteria'][0]['evidence'][0]['quote'],TEXT)
        self.assertEqual(result['criteria'][1]['pending_reason'],'processing_error')

    def test_whitespace_only_normalization_never_changes_numbers_or_negation(self):
        ref=source_ref({'paragraph_id':1,'quote':'本次仅更换冷却泵。计划停机施工'}, {1:TEXT}, {1:TEXT})
        self.assertEqual(ref['quote'],'本次仅更换冷却泵。\n 计划停机施工')
        for source,quote in [('试验不合格','试验合格'),('总投资20万元','总投资200万元'),(TEXT,'本次仅更换冷却泵...完工后调试。')]:
            with self.assertRaises(ValueError):source_ref({'paragraph_id':1,'quote':quote},{1:source},{1:source})

    def test_missing_criterion_does_not_discard_verified_sibling(self):
        item=decision();item['criteria'].pop()
        result=validate(item,check(),{1:TEXT})
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['criteria'][0]['result'],'pass')
        self.assertIn('遗漏',result['criteria'][1]['processing_error'])

    def test_windowed_paragraph_reference_uses_original_without_omission_marker(self):
        source='设备A运行正常。'+'中间记录。'*1800+'设备B需要停机。'
        window=source[:40]+'\n[…省略…]\n'+source[-40:]
        ref=source_ref({'paragraph_id':1},{1:window},{1:source})
        self.assertEqual(ref['quote'],source)
        self.assertNotIn('省略',ref['quote'])

    def test_scope_exclusion_uses_rule_level_evidence(self):
        item=dict(applicability='not_applicable',reason='本次仅更换冷却泵，不涉及汽轮机通流',criteria=[],applicability_evidence=[{'paragraph_id':1}])
        result=validate(item,check(),{1:TEXT})
        self.assertEqual(result['result'],'not_applicable')
        self.assertEqual(result['status'],'completed')
        with self.assertRaises(ValueError):validate({**item,'applicability_evidence':[]},check(),{1:TEXT})
        c=check();c['id']='FSR-C1'
        with self.assertRaises(ValueError):validate(item,c,{1:TEXT})

    def test_repair_changes_only_failed_child_and_cannot_change_scope(self):
        original=validate(decision(),check(),{1:TEXT})
        item=decision()
        item['criteria'][0]['result']='fail'
        item['criteria'][1]['evidence']=[{'paragraph_id':1}]
        repaired=validate(item,check(),{1:TEXT})
        merged=merge_repair(original,repaired)
        self.assertEqual([c['result'] for c in merged['criteria']],['pass','pass'])
        self.assertEqual(merged['status'],'completed')
        self.assertNotIn('模型输出未完成核验',merged['reason'])
        changed=merge_repair(original,{**repaired,'applicability':'not_applicable'})
        self.assertEqual(changed['applicability'],'applicable')
        self.assertEqual(changed['status'],'partial')

    def test_failed_repair_preserves_audited_child_through_pipeline(self):
        gateway=Mock();gateway.config.return_value={'enabled':True,'key':'test','model':'any-provider'}
        gateway.json.side_effect=[{'judgments':[decision()]},{'judgments':[decision()]},TimeoutError()]
        results,status=SemanticJudge(gateway).evaluate({},[check()])
        self.assertEqual(results['custom-rule']['status'],'partial')
        self.assertEqual(results['custom-rule']['criteria'][0]['result'],'pass')
        self.assertEqual(status['partial_checks'],1)
        self.assertEqual(status['status'],'partial')
        self.assertEqual(gateway.json.call_count,3)

    def test_scoring_preserves_good_children_and_separates_pending_causes(self):
        run=rubric_run();c=run['checks'][0]
        c['result']='pending';c['judgment']['status']='partial'
        c['judgment']['criteria'][0].update(result='pending',pending_reason='processing_error')
        c['judgment']['criteria'][1].update(result='pending',pending_reason='missing_evidence')
        q=quality_score(run)
        self.assertEqual(q['dimensions'][0]['contribution'],6)
        self.assertEqual(q['processing_pending_points'],3)
        self.assertEqual(q['evidence_pending_points'],3)
        self.assertFalse(q['comparable'])
        self.assertEqual(q['scoring_status'],'processing_partial')
        self.assertIsNone(q['grade'])
        c['reviewed_by']='expert';c['result']='pass'
        self.assertEqual(quality_score(run)['processing_pending_points'],0)

    def test_complete_processing_failure_does_not_publish_zero_quality(self):
        run=rubric_run()
        for c in run['checks']:
            c['result']='pending';c['judgment']={'status':'unavailable','criteria':[]}
        q=quality_score(run)
        self.assertIsNone(q['total'])
        self.assertEqual(q['processing_pending_points'],100)

    def test_bad_calculation_mapping_is_a_repairable_processing_error(self):
        c=check();c['id']='FSR-C7';c['evidence_policy']=EVIDENCE_POLICY
        c['rule']['criteria']=['工程总投资与费用分项完整且闭合']
        item=dict(applicability='applicable',coverage_complete=True,reason='校核费用',criteria=[dict(
            criterion='工程总投资与费用分项完整且闭合',result='pass',reason='模型认为闭合',
            evidence=[dict(paragraph_id=1,quote='100万元',value='100',unit='万元')],
            calculation=dict(operation='sum',operand_indexes=[99],result_index=0,complete=True))])
        result=validate(item,c,{1:'100万元'})
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['criteria'][0]['pending_reason'],'processing_error')
        self.assertTrue(result['processing_errors'])

    def test_human_review_overrides_old_processing_failures(self):
        run=rubric_run();cap=next(c for c in run['checks'] if c['id']=='FSR-CAP49')
        cap.update(result='pass',reviewed_by='expert',judgment={'status':'rejected','criteria':[]})
        self.assertTrue(quality_score(run)['comparable'])
        c=run['checks'][0]
        c.update(result='pending',reviewed_by='expert',reason='现场记录尚待补充',judgment={'status':'rejected','criteria':[]})
        q=quality_score(run)
        self.assertEqual(q['processing_pending_points'],0)
        self.assertEqual(q['evidence_pending_points'],12)
        self.assertEqual(q['scoring_status'],'pending')
        self.assertEqual(q['dimensions'][0]['items'][0]['reason'],'现场记录尚待补充')

    def test_processing_error_is_not_report_defect_in_project_conclusion(self):
        c=check();c['rule'].update(name='施工条件',source_metadata={'severity':2})
        c.update(result='pending',judgment=validate(decision(),c,{1:TEXT}))
        result=project_conclusion([c])
        self.assertFalse(result['issues'])
        self.assertTrue(result['provisional'])

    def test_browser_avoids_ranking_incomplete_processing(self):
        root=Path(__file__).resolve().parents[1]
        script="const fs=require('fs'),vm=require('vm');global.document={addEventListener(){}};vm.runInThisContext(fs.readFileSync('quality-score.js','utf8'));let a={quality_score:{policy:'fsr-70-30-v5',total:80,comparable:false}};process.stdout.write(JSON.stringify([comparisonScore(a),comparisonScore({quality_score:{policy:'old',total:60}})]));"
        self.assertEqual(json.loads(subprocess.check_output(['node','-e',script],cwd=root)),[None,60])


if __name__=='__main__':unittest.main()

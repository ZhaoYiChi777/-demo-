import unittest
from copy import deepcopy
from unittest.mock import Mock

from hybrid_retrieval import HybridRetriever
from semantic_judgment import SemanticJudge, validate
from evidence_checks import EVIDENCE_POLICY, calculate
from quality_scoring import quality_score
from test_review_upgrade import rubric_run


def gateway(fn):
    g = Mock()
    g.config.return_value = dict(enabled=True,key='offline',model='offline')
    g.json.side_effect = fn
    return g


def ref(pid, quote, obj='本项目', state='scope', value='', unit=''):
    return dict(paragraph_id=pid,quote=quote,object=obj,state=state,value=value,unit=unit)


def decision(rid, names, refs, result='pass'):
    return dict(rule_id=rid,applicability='applicable',coverage_complete=True,reason='按原文核验。',
        criteria=[dict(criterion=n,result=result,finding_type={'pass':'supported','fail':'contradiction','pending':'missing_evidence'}[result],
                       reason='按对应对象和状态核验。',evidence=deepcopy(refs)) for n in names])


class EvidencePipelineTests(unittest.TestCase):
    def test_old_defect_cannot_earn_new_design_safety_points(self):
        run = rubric_run()
        c = next(c for c in run['checks'] if c['id'] == 'FSR-B2')
        c['evidence_policy'] = EVIDENCE_POLICY
        names = c['rule']['criteria']
        old = '旧系统全厂断电时，气缸失效，逆止门不能迅速关闭。'
        new = '拟采用双排气路改善逆止门快关可靠性。'
        d = decision(c['id'], names, [ref(1, new, state='after')])
        d['criteria'][1]['evidence'] = [ref(2, old, state='before')]
        c['judgment'] = validate(d, c, {1:new, 2:old})
        c['result'] = c['judgment']['result']
        self.assertEqual(c['result'], 'pending')
        self.assertEqual([k['result'] for k in c['judgment']['criteria']], ['pass','pending'])
        q = quality_score(run)
        dimension = next(d for d in q['dimensions'] if d['id'] == 'B2')
        self.assertEqual((dimension['contribution'], dimension['unresolved']), (4, 4))

    def test_explicit_new_design_response_keeps_safety_credit(self):
        name = '明确失电、失气或设备故障时的安全状态与保护措施'
        old = '原装置失电不能关阀。'
        new = '拟建装置失电或失气时由弹簧复位关闭逆止门。'
        c = dict(id='FSR-B2', evidence_policy=EVIDENCE_POLICY, rule=dict(mode='manual', criteria=[name]))
        d = decision(c['id'], [name], [ref(1,old,state='before'), ref(2,new,state='after')])
        self.assertEqual(validate(d,c,{1:old,2:new})['result'], 'pass')

    def test_old_fault_is_valid_necessity_evidence(self):
        name = '本厂缺陷、检测异常或本厂预防性风险评估支撑改造必要性'
        quote = '本厂旧逆止门关闭超时。'
        c = dict(id='TUR-NEC-004', evidence_policy=EVIDENCE_POLICY, rule=dict(mode='manual', criteria=[name]))
        d = decision(c['id'], [name], [ref(1,quote,state='before')])
        self.assertEqual(validate(d,c,{1:quote})['result'], 'pass')

    def test_uncertain_applicability_cannot_earn_criterion_points(self):
        run = rubric_run()
        c = run['checks'][0]
        c.update(result='pending', reason='尚未明确适用范围。')
        c['judgment'].update(result='pending', applicability='uncertain')
        q = quality_score(run)
        self.assertEqual((q['raw_total'], q['coverage']), (88, 88))
        self.assertIsNone(q['grade'])
        self.assertTrue(all(k['result']=='pending' for k in q['dimensions'][0]['items']))
        c.update(result='pass', reviewed_by='人工确认')
        self.assertEqual(quality_score(run)['raw_total'], 100)

    def test_old_reference_shape_keeps_supported_verdict(self):
        name='说明本次设备或系统的运行现状和边界'
        d=decision('FSR-C1',[name],[dict(paragraph_id=1,quote='本次优化1号机抽汽逆止门。')])
        c=dict(id='FSR-C1',evidence_policy=EVIDENCE_POLICY,rule=dict(mode='manual',criteria=[name]))
        out=validate(d,c,{1:'本次优化1号机抽汽逆止门。'})
        self.assertEqual(out['result'],'pass')
        self.assertTrue(out['criteria'][0]['format_warnings'])

    def test_irrelevant_calculation_does_not_penalize_necessity(self):
        name='本厂缺陷、检测异常或本厂预防性风险评估支撑改造必要性'
        d=decision('TUR-NEC-004',[name],[dict(paragraph_id=1,quote='本厂逆止门关闭超时。')])
        d['criteria'][0]['calculation']=dict(operation='multiply',operand_indexes=[0,3],result_index=1)
        c=dict(id='TUR-NEC-004',evidence_policy=EVIDENCE_POLICY,rule=dict(mode='manual',criteria=[name]))
        out=validate(d,c,{1:'本厂逆止门关闭超时。'})
        self.assertEqual(out['result'],'pass')
        self.assertIn('ignored_calculation',out['criteria'][0])

    def test_presence_of_total_is_not_a_total_consistency_test(self):
        name='填写工程总投资'
        text='设备费120万元，工程总投资0万元。'
        d=decision('TUR-DOC-003',[name],[dict(paragraph_id=1,quote=text)])
        d['criteria'][0]['calculation']=dict(operation='sum',operand_indexes=[0],result_index=0)
        c=dict(id='TUR-DOC-003',evidence_policy=EVIDENCE_POLICY,rule=dict(mode='manual',criteria=[name]))
        out=validate(d,c,{1:text})
        self.assertEqual(out['result'],'pass')
        self.assertNotIn('arithmetic',out['criteria'][0])
        self.assertEqual(out['criteria'][0]['model_result'],'pass')

    def test_same_fee_comparison_does_not_inherit_project_total_conflict(self):
        name='同一费用在各章节的金额一致或差异已有解释'
        text='设备费120万元，工程总投资0万元。'
        d=decision('TUR-INV-005',[name],[dict(paragraph_id=1,quote='设备费120万元'),dict(paragraph_id=2,quote='设备费120万元')])
        d['criteria'][0]['calculation']=dict(operation='multiply',operand_indexes=[1,0],result_index=2)
        c=dict(id='TUR-INV-005',evidence_policy=EVIDENCE_POLICY,rule=dict(mode='manual',criteria=[name]))
        out=validate(d,c,{1:text,2:'设备费120万元'})
        self.assertEqual(out['result'],'pass')
        self.assertNotIn('arithmetic',out['criteria'][0])

    def test_recover_real_report_amounts_from_old_refs_and_wrong_indexes(self):
        text='（二）设计费；0万元。（三）调试费；0万元。（四）设备费；120万。（五）施工费（其中人工费）；0万元。（六）主材料费；0万元。（七）工程总投资；0万元。'
        name='工程总投资与费用分项完整且闭合'
        d=decision('FSR-C7',[name],[dict(paragraph_id=1,quote='设备费；120万。'),dict(paragraph_id=1,quote='工程总投资；0万元。')])
        d['criteria'][0]['calculation']=dict(operation='sum',operand_indexes=[0,1],result_index=1,complete=False)
        c=dict(id='FSR-C7',evidence_policy=EVIDENCE_POLICY,rule=dict(mode='manual',criteria=[name]))
        out=validate(d,c,{1:text})
        self.assertEqual(out['result'],'fail')
        self.assertEqual(out['criteria'][0]['arithmetic']['computed'],'120')
        self.assertNotIn('evidence_issue',out['criteria'][0])

    def test_explicit_table_header_units_are_accepted(self):
        refs=[ref(1,'单位：万元。设备费 | 100','设备费',value='100',unit='万元'),ref(2,'安装费20万','安装费',value='20',unit='万元'),
              ref(3,'工程总投资120万元','总投资',value='120',unit='万元')]
        self.assertEqual(calculate(dict(operation='sum',operand_indexes=[0,1],result_index=2,complete=True),refs)['status'],'matched')

    def test_failed_rule_window_does_not_contaminate_other_groups(self):
        rules = [dict(id=f'r{i}',terms=[]) for i in range(33)]
        chunks = [dict(id=1,text='甲'*15000),dict(id=2,text='乙'*2000)]
        def respond(_, p):
            if p['rules'][0]['id']=='r32' and p['paragraphs'][0]['paragraph_id']==1:
                raise TimeoutError()
            return {'matches':[]}
        found, overall = HybridRetriever(gateway(respond)).retrieve({'chunks':chunks},rules)
        self.assertEqual(overall['status'],'partial')
        self.assertEqual(found['r0']['retrieval']['status'],'complete')
        self.assertEqual(found['r32']['retrieval']['status'],'partial')
        failed = found['r32']['retrieval']['failed_windows']
        self.assertEqual([(w['paragraph_id'],w['start'],w['error']) for w in failed],[(1,0,'TimeoutError')])

    def test_quote_must_be_in_sent_window(self):
        chunks=[dict(id=1,text='前文'*9000+'最后金额')]
        def respond(_,p):
            return {'matches':[dict(rule_id='r',evidence=[dict(paragraph_id=1,quote='最后金额')])]}
        found,status=HybridRetriever(gateway(respond)).retrieve({'chunks':chunks},[dict(id='r',terms=[])])
        self.assertEqual(status['rejected_evidence'],1)
        self.assertEqual(len(found['r']['evidence']),1)

    def test_money_sum_units_precision_and_partial_items(self):
        refs=[ref(1,'设备费100万元','设备费',value='100',unit='万元'),
              ref(2,'安装费200000元','安装费',value='200000',unit='元'),
              ref(3,'总投资120万元','总投资',value='120',unit='万元')]
        spec=dict(operation='sum',operand_indexes=[0,1],result_index=2,complete=True)
        self.assertEqual(calculate(spec,refs)['status'],'matched')
        refs[2]=ref(3,'总投资0万元','总投资',value='0',unit='万元')
        self.assertEqual(calculate(spec,refs)['status'],'mismatch')
        self.assertEqual(calculate({**spec,'complete':False},refs)['status'],'mismatch')
        refs[2]=ref(3,'总投资150万元','总投资',value='150',unit='万元')
        self.assertEqual(calculate({**spec,'complete':False},refs)['status'],'partial')
        refs[1]['object']='设备费'
        with self.assertRaisesRegex(ValueError,'重复'):
            calculate(spec,refs)

    def test_quantity_times_unit_price_and_invented_number(self):
        refs=[ref(1,'阀门6台','数量',value='6',unit='台'),ref(2,'单价20万元/台','单价',value='20',unit='万元/台'),
              ref(3,'设备费120万元','设备费',value='120',unit='万元')]
        spec=dict(operation='multiply',operand_indexes=[0,1],result_index=2,complete=True)
        self.assertEqual(calculate(spec,refs)['status'],'matched')
        refs[0]['value']='8'
        with self.assertRaisesRegex(ValueError,'原文'):
            calculate(spec,refs)

    def test_arithmetic_overrides_model_pass_only_for_affected_criterion(self):
        names=['工程总投资与费用分项完整且闭合','所宣称的效益有依据']
        refs=[ref(1,'设备费120万元','设备费',value='120',unit='万元'),ref(2,'工程总投资0万元','总投资',value='0',unit='万元')]
        d=decision('FSR-C7',names,refs)
        d['criteria'][0]['calculation']=dict(operation='sum',operand_indexes=[0],result_index=1,complete=False)
        check=dict(id='FSR-C7',evidence_policy=EVIDENCE_POLICY,rule=dict(mode='manual',criteria=names))
        out=validate(d,check,{r['paragraph_id']:r['quote'] for r in refs})
        self.assertEqual([c['result'] for c in out['criteria']],['fail','pass'])
        self.assertEqual(out['criteria'][0]['arithmetic']['difference'],'120')
        self.assertIn('不将明细合计认定为审定总投资',out['reason'])

    def test_comparison_cannot_treat_before_after_as_same_basis(self):
        names=['投资、收益及计算单位前后一致']
        refs=[ref(1,'改造前投资100万元','投资','before','100','万元'),ref(2,'改造后投资120万元','投资','after','120','万元')]
        d=decision('FSR-C8',names,refs,'fail');d['criteria'][0]['comparison']='same_basis'
        check=dict(id='FSR-C8',evidence_policy=EVIDENCE_POLICY,rule=dict(mode='manual',criteria=names))
        out=validate(d,check,{r['paragraph_id']:r['quote'] for r in refs})
        self.assertEqual(out['result'],'pending')
        self.assertIn('改造前后',out['reason'])

    def test_supplement_recovers_late_evidence_before_final_decision(self):
        name='给出量化目标值或性能保证值'
        chunks=[dict(id=i,document_id=1,text='背景说明'*160,section_path='背景') for i in range(1,42)]
        chunks.append(dict(id=42,document_id=1,text='改造后阀门关闭时间为2秒。',section_path='性能保证'))
        refs=[ref(42,chunks[-1]['text'],'阀门','after','2','秒')]
        c=dict(id='FSR-C5',category='common',missing=[],rule=dict(id='FSR-C5',mode='manual',origin='rubric',criteria=[name],terms=['性能'],name='验收',standard=name,content=name),
               evidence=[],retrieval=dict(status='complete',coverage_policy='per-rule-window-v1'))
        seen=[]
        def respond(prompt,p):
            if 'evidence_ids' not in p['rules'][0]:
                seen.append('search')
                return {'matches':[dict(rule_id=c['id'],evidence=[refs[0]])]} if any(x['paragraph_id']==42 for x in p['paragraphs']) else {'matches':[]}
            seen.append('final' if p.get('supplemental_search') else 'draft')
            return {'judgments':[decision(c['id'],[name],refs if p.get('supplemental_search') else [],'pass' if p.get('supplemental_search') else 'pending')]}
        found,status=SemanticJudge(gateway(respond)).evaluate({'chunks':chunks},[c])
        self.assertEqual(found[c['id']]['result'],'pass')
        self.assertTrue(found[c['id']]['supplemental_search'])
        self.assertIn('search',seen)
        self.assertEqual(seen[-1],'final')
        self.assertTrue(any(e['id']==42 for e in c['evidence']))
        found,_=SemanticJudge(gateway(respond)).evaluate({'chunks':chunks},[c],independent=True)
        self.assertTrue(found[c['id']]['independent_agreement'])
        self.assertEqual(found[c['id']]['result'],'pass')

    def test_failed_supplement_only_leaves_global_criterion_pending(self):
        names=['投资、收益及计算单位前后一致','说明本次施工安排']
        chunks=[dict(id=1,text='设备费100万元。'),dict(id=2,text='工程总投资100万元。随机组停机施工。')]
        refs=[ref(1,chunks[0]['text'],'设备费',value='100',unit='万元'),ref(2,chunks[1]['text'],'总投资',value='100',unit='万元')]
        c=dict(id='FSR-C8',category='common',missing=[],rule=dict(id='FSR-C8',mode='manual',origin='rubric',criteria=names,terms=['投资'],name='一致性',standard='一致性',content='一致性'),
            evidence=[dict(id=1,text=chunks[0]['text']),dict(id=2,text=chunks[1]['text'])],retrieval=dict(status='partial',coverage_policy='per-rule-window-v1',failed_windows=[dict(paragraph_id=3,start=0,end=100,error='TimeoutError')]))
        def respond(_,p):
            if 'evidence_ids' not in p['rules'][0]:
                raise TimeoutError()
            return {'judgments':[decision(c['id'],names,refs)]}
        found,_=SemanticJudge(gateway(respond)).evaluate({'chunks':chunks},[c])
        self.assertEqual([k['result'] for k in found[c['id']]['criteria']],['pending','pass'])
        self.assertEqual(found[c['id']]['status'],'completed')

    def test_local_failure_preserves_supported_criterion_and_points(self):
        run=rubric_run('fsr-70-30-v4')
        run['retrieval']=dict(status='partial',coverage_policy='per-rule-window-v1')
        c=next(c for c in run['checks'] if c['id']=='FSR-C1')
        c['retrieval']=dict(status='partial',coverage_policy='per-rule-window-v1')
        c['judgment'].update(result='pending',evidence_policy=EVIDENCE_POLICY)
        c['judgment']['criteria'][0]['result']='pending'
        c['result']='pending'
        q=quality_score(run)
        dimension=next(d for d in q['dimensions'] if d['id']=='C1')
        self.assertEqual(dimension['contribution'],9)
        self.assertEqual(q['total'],92)
        cap=next(c for c in run['checks'] if c['id']=='FSR-CAP49')
        cap.update(result='pending',judgment=dict(status='unavailable',result='pending',criteria=[]))
        q=quality_score(run)
        self.assertEqual(q['total'],92)
        self.assertTrue(q['cap_unresolved'])
        self.assertEqual(q['lower_bound'],49)
        self.assertIsNone(q['grade'])

    def test_repair_failure_does_not_erase_other_rules(self):
        checks=[dict(id=rid,category='special',missing=[],evidence=[dict(id=1,text='停机后调试。')],retrieval=dict(status='complete'),
            rule=dict(id=rid,mode='manual',criteria=['安排调试'],name='调试',standard='调试',content='调试')) for rid in ('a','b')]
        good=decision('a',['安排调试'],[ref(1,'停机后调试。')])
        bad=decision('b',['安排调试'],[ref(1,'不存在的原文')])
        def respond(_,p):
            if p.get('validation_error'):
                raise TimeoutError()
            return {'judgments':[good,bad]}
        found,status=SemanticJudge(gateway(respond)).evaluate({},checks)
        self.assertEqual(found['a']['result'],'pass')
        self.assertEqual(found['b']['status'],'unavailable')
        self.assertEqual(status['status'],'partial')

    def test_invalid_batch_json_recovers_in_small_groups(self):
        import json
        checks=[dict(id=rid,category='special',missing=[],evidence=[dict(id=1,text='停机后调试。')],retrieval=dict(status='complete'),
            rule=dict(id=rid,mode='manual',criteria=['安排调试'],name='调试',standard='调试',content='调试')) for rid in ('a','b','c')]
        sizes=[]
        def respond(_,p):
            sizes.append(len(p['rules']))
            if len(p['rules'])>2:
                raise json.JSONDecodeError('invalid','',0)
            return {'judgments':[decision(r['id'],['安排调试'],[ref(1,'停机后调试。')]) for r in p['rules']]}
        found,status=SemanticJudge(gateway(respond)).evaluate({},checks)
        self.assertTrue(all(j['result']=='pass' for j in found.values()))
        self.assertEqual(status['status'],'complete')
        self.assertEqual(sizes[:2],[3,3])
        self.assertTrue(all(n<=2 for n in sizes[2:]))
        self.assertEqual(status['batches'][0]['recovered_error'],'JSONDecodeError')


if __name__=='__main__':
    unittest.main()

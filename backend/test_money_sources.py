import json
from pathlib import Path
import unittest

from evidence_checks import EVIDENCE_POLICY, source_expense_sum
from judgment_contract import validate
from money_sources import expense_facts, resolve_expense_ref
from test_evidence_pipeline import decision


NAME = '工程总投资与费用分项完整且闭合'
ROOT = Path(__file__).resolve().parents[1]


def review(paragraphs, refs, calculation, documents=None):
    check = dict(id='FSR-C7', evidence_policy=EVIDENCE_POLICY,
                 rule=dict(mode='manual', criteria=[NAME]), _paragraph_documents=documents or {})
    item = decision(check['id'], [NAME], refs)
    item['criteria'][0]['calculation'] = calculation
    return validate(item, check, paragraphs)['criteria'][0]


class MoneySourceTests(unittest.TestCase):
    def test_header_unit_and_reformatted_row_become_exact_source(self):
        text = '单位：万元 序号 | 项目 | 费用 1 | 设备费 | 190 | 2 | 施工费 | 60 | 合计 | 合计 | 250'
        refs = [dict(paragraph_id=1, quote=f'{label}：{value}', value=str(value), unit='万元')
                for label,value in [('设备费',190),('施工费',60),('合计',250)]]
        c = review({1:text}, refs, dict(operation='sum',operand_indexes=[0,1],result_index=2,complete=True))
        self.assertEqual(c['arithmetic']['status'], 'matched')
        self.assertEqual(c['result'], 'pass')
        self.assertTrue(all(r['quote'] in text for r in c['evidence']))

    def test_paragraph_indexes_are_rebuilt_without_subtotal_double_count(self):
        text = '费用（万元） 序号 | 项目 | 费用 1 | 设备费 | 190 | 2 | 主材费 | 8 | 3 | 材料费 | 2 | 4 | 施工费 | 60 | 合计 | 合计 | 260 | 序号 | 名称 | 数量 | 单价 | 总价 1 | 阀座 | 2 | 60 | 120 | 合计 | 合计 | 190'
        c = review({1:text}, [{'paragraph_id':1}], dict(operation='sum',operand_indexes=[0,1,2,3],result_index=4,complete=True))
        self.assertEqual(c['arithmetic']['computed'], '260')
        self.assertEqual(c['arithmetic']['status'], 'matched')
        self.assertNotIn('processing_error',c)

    def test_unitless_quote_only_corroborates_within_known_document(self):
        texts = {1:'设备费：190', 2:'设备费190万元'}
        ref = dict(paragraph_id=1,quote=texts[1],value='190',unit='万元')
        self.assertEqual(resolve_expense_ref(ref,texts,{1:5,2:5})['paragraph_id'],2)
        self.assertEqual(resolve_expense_ref(ref,texts,{1:5,2:6}),ref)
        self.assertEqual(resolve_expense_ref(ref,texts),ref)
        self.assertFalse(expense_facts({1:texts[1]}))

    def test_unknown_amount_cannot_be_mapped_to_existing_row(self):
        texts={1:'单位：万元 设备费 | 190'}
        ref=dict(paragraph_id=1,quote='设备费：199',value='199',unit='万元')
        self.assertEqual(resolve_expense_ref(ref,texts),ref)

    def test_partial_table_does_not_replace_project_total(self):
        texts={1:'工程总投资150万元',2:'单位：万元 设备费 | 设备费 | 60 | 施工费 | 施工费 | 40 | 合计 | 合计 | 100'}
        c=review(texts,[{'paragraph_id':1},{'paragraph_id':2}],
                 dict(operation='sum',operand_indexes=[3,4],result_index=0,complete=True),{1:1,2:1})
        self.assertEqual(c['result'],'pending')
        self.assertEqual(c['arithmetic']['reported'],'150')
        self.assertEqual(c['arithmetic']['status'],'partial')

    def test_inline_unit_overrides_table_header_and_unit_price_is_not_cost(self):
        texts={1:'单位：万元 设备费 | 设备费 | 100元 | 施工费 | 施工费 | 2 | 合计 | 合计 | 2.01'}
        arithmetic,_=source_expense_sum({},texts,complete=True)
        self.assertEqual(arithmetic['status'],'matched')
        self.assertEqual(arithmetic['computed'],'2.01')
        self.assertFalse(expense_facts({1:'设备费100万元/台'}))

    def test_included_material_is_not_added_to_parent_cost(self):
        text='工程总投资150万元。设备费100万元（含材料费60万元），施工费50万元。'
        refs=[dict(paragraph_id=1,quote=q,value=v,unit='万元',object=o) for q,v,o in
              [('设备费100万元','100','设备费'),('施工费50万元','50','施工费'),('工程总投资150万元','150','总投资')]]
        c=review({1:text},refs,dict(operation='sum',operand_indexes=[0,1],result_index=2,complete=True))
        self.assertEqual(c['arithmetic']['status'],'matched')
        self.assertEqual(c['result'],'pass')

    def test_partial_extraction_below_total_is_not_false_failure(self):
        c=review({1:'设备费100万元，总投资150万元'},[{'paragraph_id':1}],
                 dict(operation='sum',operand_indexes=[0,9],result_index=8,complete=True))
        self.assertEqual(c['result'],'pending')
        self.assertEqual(c['arithmetic']['status'],'partial')
        self.assertNotIn('processing_error',c)

    def test_frozen_reports_preserve_real_totals_and_conflict(self):
        cases=json.loads((ROOT/'artifacts/small-baseline-20260923/inputs.json').read_text(encoding='utf-8'))
        expected={'R03':('260','260','matched'),'R05':('120','0','mismatch'),'R09':('1150','1150','matched')}
        for case in cases:
            with self.subTest(case=case['case_id']):
                arithmetic, refs=source_expense_sum({}, {p['id']:p['text'] for p in case['material']['chunks']},complete=True)
                self.assertEqual(tuple(arithmetic[k] for k in ('computed','reported','status')), expected[case['case_id']])
                self.assertEqual(len({r['object'] for r in refs}),len(refs))

    def test_actual_deepseek_and_qwen_failed_outputs_now_calculate(self):
        for model in ('deepseek-v4.1-flash','qwen3.8-flash'):
            for call in ('call-006.json','call-007.json'):
                with self.subTest(model=model,call=call):
                    record=json.loads((ROOT/'artifacts/review-generalization-20260923'/model/call).read_text(encoding='utf-8'))
                    payload=json.loads(record['request']['messages'][-1]['content'])
                    answer=json.loads(record['answer'])
                    rule=next(r for r in payload['rules'] if r['id']=='FSR-C7')
                    check=dict(id=rule['id'],rule=rule,evidence_policy=EVIDENCE_POLICY,
                               _paragraph_documents={p['paragraph_id']:p['document_id'] for p in payload['paragraphs']})
                    out=validate(next(j for j in answer['judgments'] if j['rule_id']=='FSR-C7'),check,
                                 {p['paragraph_id']:p['text'] for p in payload['paragraphs']})
                    c=out['criteria'][0]
                    self.assertNotIn('processing_error',c)
                    self.assertEqual(c['arithmetic']['computed'],'260')
                    self.assertEqual(c['arithmetic']['status'],'matched')


if __name__=='__main__':
    unittest.main()

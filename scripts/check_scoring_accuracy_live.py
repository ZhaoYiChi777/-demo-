"""Targeted model checks for adjusted feasibility criteria; never writes project records."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from main import model_gateway
from semantic_judgment import SemanticJudge
from evidence_checks import EVIDENCE_POLICY
from fsr_rubric import COMMON


SAFETY = '明确失电、失气或设备故障时的安全状态与保护措施'
CASES = [
    ('old_fault_and_general_upgrade', SAFETY,
     '本次优化抽汽逆止门控制装置。旧系统全厂断电时气缸失效，逆止门不能迅速关闭。拟建方案采用大流量双排气控制系统，提高快关可靠性。', 'pending'),
    ('new_design_fault_response', SAFETY,
     '本次优化抽汽逆止门控制装置。拟建装置失电或失气时，弹簧复位使逆止门关闭；控制故障同样触发复位关阀。', 'pass'),
    ('old_fault_supports_necessity', '本厂缺陷、检测异常或本厂预防性风险评估支撑改造必要性',
     '本厂旧抽汽逆止门经活动试验发现关闭超时，需优化控制装置以消除倒流风险。', 'pass'),
    ('same_fee_not_project_total', '同一费用在各章节的金额一致或差异已有解释',
     '设备费用章节列设备费120万元。投资表中设备费120万元、工程总投资0万元。', 'pass'),
    ('recommended_vs_current', COMMON[3][3][0],
     '现状方案的控制排气能力不足，推荐扩大排气通道，沿用现有阀体。推荐方案可利用检修窗口实施，改善关闭速度，减少更换整阀工作量，因此选择该方案。', 'pass'),
]


def main():
    out = ROOT / 'artifacts' / 'scoring-accuracy-20260923'
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, criterion, text, expected in CASES:
        rule = dict(id=name, name=name, origin='custom', category='special', mode='manual',
                    criteria=[criterion], standard=criterion, content='按已调整的可研阶段口径，仅核验本条判据。')
        check = dict(id=name, rule=rule, category='special', missing=[],
                     evidence=[dict(id=1, text=text)], evidence_policy=EVIDENCE_POLICY,
                     retrieval=dict(status='complete'))
        # Each scenario is a separate scope so a positive sample cannot prove a negative one.
        results, status = SemanticJudge(model_gateway).evaluate(
            dict(scope_name=name, brief='', sources=[]), [check])
        actual = results[name]
        rows.append(dict(name=name, input=text, criterion=criterion, expected=expected,
                         actual=actual, status=status,
                         matched=actual['result'] == expected and actual['status'] == 'completed'))
        (out / 'controlled-live.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"{name}: expected={expected}, actual={actual['result']}, status={actual['status']}", flush=True)
        if status['status'] in ('disabled', 'unavailable'):
            break
    if len(rows) != len(CASES) or not all(row['matched'] for row in rows):
        raise SystemExit('Model acceptance incomplete or mismatched; see controlled-live.json')
    print('PASS: 5 isolated model examples; project records unchanged', flush=True)


if __name__ == '__main__':
    main()

"""Reproduce a retrospective, assistant-labelled spot check; no model calls."""
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/baseline-40-20260923'
OUT = ROOT / 'artifacts/baseline-accuracy-audit-20260923'
MODELS = {1: 'deepseek-v4.1-flash', 2: 'qwen3.8-flash', 3: 'kimi-k3', 4: 'glm-5.2'}
# Labels are retrospective assistant interpretations, not independent expert truth.
REFERENCES = [
    ('R03', 'FSR-C1:2', 'pass', [910], '本厂两侧阀座硬度177–259HB、223–249HB及检修记录，明确提供检测和故障依据。'),
    ('R03', 'FSR-C5:1', 'pass', [912, 916], '改造后阀座硬度不低于380HB，是明确目标值。'),
    ('R05', 'FSR-C1:2', 'pass', [47, 48], '六个抽汽逆止门检测关闭时间1102–2410ms，明确对应本厂设备。'),
    ('R05', 'FSR-C5:1', 'pass', [55, 56, 57], '改造效果明确为关闭时间小于1000ms。'),
    ('R07', 'FSR-C1:2', 'pass', [8], '2024年6月19日、7月31日两次本厂油管接头渗漏事件是故障记录。'),
    ('R07', 'FSR-C5:1', 'pending', [15, 16, 17], '有功能和配置目标；定量比较填无，性能参数需投标方提供，量化保证值证据不足。'),
    ('R08', 'FSR-C1:2', 'pending', [27, 28, 29, 30], '正常、无缺陷是现状陈述；铭牌参数和投产日期不是本次高旁检测记录。按需要本厂检测或运行数据的口径，证据不足应待确认，不等于设备不合格。'),
    ('R08', 'FSR-C5:1', 'pending', [36, 37, 38], '热耗系数是漏汽影响估算，不是改造保证值；完全切断蒸汽流是功能目标。按需明确性能保证值的口径，待确认。'),
    ('R09', 'FSR-C1:2', 'pending', [22, 23], '仅技术原理、预期效果和预算，没有本厂运行检测或故障记录。'),
    ('R09', 'FSR-C5:1', 'pending', [22], '零出力3000rpm出现在技术现状描述中；项目目标未明确供热能力、调峰深度等保证值。按需项目目标而非原理参数的口径，待确认。'),
]


def load_run(case, method, model):
    path = BASE / f'{case}-{method}-{model}-01/run.json'
    return path, json.loads(path.read_text(encoding='utf-8'))


def metrics(rows):
    count = len(rows)
    return {
        'n': count,
        'exact_correct': sum(r['exact_correct'] for r in rows),
        'award_correct': sum(r['award_correct'] for r in rows),
        'unsupported_pass': sum(r['reference'] != 'pass' and r['result'] == 'pass' for r in rows),
        'supported_not_pass': sum(r['reference'] == 'pass' and r['result'] != 'pass' for r in rows),
        'missing_as_fail': sum(r['reference'] == 'pending' and r['result'] == 'fail' for r in rows),
        'results': dict(Counter(r['result'] for r in rows)),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    labels, rows = [], []
    for case, cid, expected, ids, rationale in REFERENCES:
        path, source = load_run(case, 'system', 3)
        check = next(c for c in source['checks'] if c['rule']['id'] == cid.split(':')[0])
        criterion_text = check['rule']['criteria'][int(cid.split(':')[1]) - 1]
        excerpts = [{'paragraph_id': c['id'], 'text': c['text']} for c in source['chunks'] if c['id'] in ids]
        assert len(excerpts) == len(ids)
        labels.append(dict(case=case, criterion_id=cid, criterion=criterion_text,
                           reference=expected, rationale=rationale, source=str(path), excerpts=excerpts))
        for number, model in MODELS.items():
            for method in ('direct', 'system'):
                run_path, run = load_run(case, method, number)
                check = next(c for c in run['checks'] if c['rule']['id'] == cid.split(':')[0])
                criterion = next(c for c in check['judgment']['criteria'] if c['criterion_id'] == cid)
                assert criterion['result'] in ('pass', 'pending', 'fail')
                assert not criterion.get('processing_error')
                assert criterion.get('pending_reason') in (None, 'missing_evidence')
                result = criterion['result']
                rows.append(dict(case=case, model=model, method=method, criterion_id=cid,
                                 reference=expected, result=result,
                                 exact_correct=result == expected,
                                 award_correct=(result == 'pass') == (expected == 'pass'),
                                 reason=criterion.get('reason', ''),
                                 evidence_ids=json.dumps([e.get('paragraph_id') for e in criterion.get('evidence', [])]),
                                 check_status=check['judgment']['status'], source=str(run_path)))
    assert len(rows) == 80
    assert len({(r['case'], r['model'], r['method'], r['criterion_id']) for r in rows}) == 80
    summary = {
        'label_author': 'Codex assistant; retrospective, not independent expert labels',
        'selection': 'Two fixed criteria across all five reports; selected after reviewing prior method differences.',
        'unit': '10 unique report-criterion pairs, each judged by four models and two methods',
        'methods': {m: metrics([r for r in rows if r['method'] == m]) for m in ('direct', 'system')},
        'models': {model: {m: metrics([r for r in rows if r['model'] == model and r['method'] == m])
                           for m in ('direct', 'system')} for model in MODELS.values()},
        'exclude_R08': {m: metrics([r for r in rows if r['method'] == m and r['case'] != 'R08'])
                        for m in ('direct', 'system')},
        'disagreements': [r for r in rows if not r['exact_correct']],
    }
    for name, value in [('reference-labels.json', labels), ('summary.json', summary)]:
        (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    with (OUT / 'criterion-comparison.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (BASE / 'results.csv').open(encoding='utf-8-sig', newline='') as f:
        runs = list(csv.DictReader(f))
    assert len(runs) == 40
    direct, system = summary['methods']['direct'], summary['methods']['system']
    report = [
        '# 直接评审与系统评审：准确度能力对照',
        '',
        '结论：本次小范围抽核中，两种方法的三态判定一致率均为95%。系统减少了一次无依据给分，但又将一次证据缺失误判为不符合，尚不能据此认定整体准确度更高。系统优势目前主要体现为评分可比率更高。',
        '',
        '## 抽核口径',
        '',
        '使用首轮40次真实评审的冻结输出，不重跑模型、不调整原始评分。逐项阅读冻结的报告解析文本，选取全部5份报告共同的两项判据：FSR-C1:2（本厂故障记录、检测值或运行基线数据）和FSR-C5:1（量化目标值或性能保证值）。共10个不同的报告—判据单元，每个由4种模型、2种方法判断，合计80条输出、每种方法40条。不是40份独立报告。',
        '',
        '参考标签由本助手在已看过部分测试差异后事后制定，未经过独立专家盲审；结果是诊断性抽核一致率，不是全系统准确率，也不是独立测试集成绩。未覆盖图表识别、全部业务规则或总分与专家评分的误差。',
        '',
        '判定分为符合（pass）、不符合（fail）、待确认（pending）。遵循冻结规则中的“缺少证据保留待确认”：文字没有提供依据时标记待确认，不能据此认定设备或方案已经不符合。只在通过与否层面合并fail和pending时，称为“给分判断一致率”；此指标不评价两者语义是否正确。',
        '',
        '## 对照结果',
        '',
        '| 指标 | 直接评审 | 系统评审 |',
        '|---|---:|---:|',
        f"| 三态判定与参考标签一致 | {direct['exact_correct']}/40（95%） | {system['exact_correct']}/40（95%） |",
        f"| 给分判断一致（仅通过/非通过） | {direct['award_correct']}/40（95%） | {system['award_correct']}/40（97.5%） |",
        f"| 证据不足仍判通过 | {direct['unsupported_pass']}次 | {system['unsupported_pass']}次 |",
        f"| 已有明确证据却未判通过 | {direct['supported_not_pass']}次 | {system['supported_not_pass']}次 |",
        f"| 缺少证据却判为不符合 | {direct['missing_as_fail']}次 | {system['missing_as_fail']}次 |",
    ]
    valid, times = {}, {}
    for method in ('direct', 'system'):
        selected = [r for r in runs if r['method'] == method]
        valid[method] = sum(r['comparable'] == 'True' for r in selected)
        times[method] = sum(float(r['elapsed_seconds']) for r in selected) / len(selected) / 60
    report += [
        f"| 全部运行评分可比率（流程指标，非准确率） | {valid['direct']}/20（85%） | {valid['system']}/20（100%） |",
        f"| 每份报告平均评审用时 | {times['direct']:.2f}分钟 | {times['system']:.2f}分钟 |",
        '',
        '## 分模型抽核',
        '',
        '| 模型 | 直接评审三态一致 | 系统评审三态一致 |',
        '|---|---:|---:|',
    ]
    for model, values in summary['models'].items():
        report.append(f"| {model} | {values['direct']['exact_correct']}/10 | {values['system']['exact_correct']}/10 |")
    report += ['', '每种模型仅10个抽核单元，不能据此进行模型综合能力排名。', '', '## 四处具体差异', '']
    for r in summary['disagreements']:
        report += [f"- {r['case']} / {r['model']} / {r['method']} / {r['criterion_id']}：参考为{r['reference']}，实际为{r['result']}。模型理由：{r['reason']}"]
    report += [
        '',
        '四处差异均在R08高旁系统报告：DeepSeek直接评审将“本厂正常、无缺陷”当作满足基线数据要求；Kimi直接评审、智谱系统评审将漏汽热耗影响估算当作改造目标或保证值；Kimi系统评审则把缺少本厂记录判为fail。原文段落29–30、36–38及完整判断均保存在明细中。',
        '',
        '## 口径敏感性与能力解释',
        '',
        'R08存在真实的规则解释空间：“正常、无缺陷”是否足以构成基线证据，“完全切断蒸汽流”是否可以直接作为性能保证。这里采用需要检测/运行依据和明确性能值的口径；现行规则未将这些边界完全写清，因此四处差异称为“相对于本次参考口径的偏差”。排除R08后，直接评审与系统评审均为32/32一致，当前样本不再显示差别。R09中的“零出力3000rpm”按技术原理背景而非已承诺的项目验收目标处理；若采用其他解释，应同步调整两种方法的参考标签。',
        '',
        '基础事实提取：R03阀座硬度、R05关闭时间、R07两次故障记录，两种方法均正确识别；本轮未显示系统在这些显式证据上的优势。证据边界：系统修正了部分宽松通过，但智谱系统仍混淆了影响系数与目标值，说明增加流程不能消除语义误判。状态语义：Kimi系统把证据缺失判成不符合，说明更严格不等于更准确。',
        '',
        '输出稳定性：系统全部20次评分可比，直接评审17次可比；这衡量流程能否稳定产出可用于比较的分数。两种方法仍有未解决的业务判据，评分可比不等于报告全部规则判定完成。抽核保留所有40次运行；即使某次总分不可比，本次选取的具体判据仍有有效输出，80条中没有所选判据的处理失败。',
        '',
        '当前最合适的结论是：直接评审可作为快速初筛，系统评审更适合需要逐项追溯与稳定汇总的场景；系统准确度提升尚需独立参考标签验证。先由专业人员裁定现有争议项，再扩大到关键业务判据，复用现有输出即可继续比较，无需先增加模型调用。',
        '',
        '## 可复现文件',
        '',
        '- reference-labels.json：10条参考标签、理由及原文段落。',
        '- criterion-comparison.csv：80条逐项输出、原始理由、证据段落号和来源路径。',
        '- summary.json：两种方法、各模型及排除R08后的统计。',
        '- 重建命令（项目根目录）：`.\\.runtime\\Scripts\\python.exe scripts/audit_baseline_accuracy.py`。',
    ]
    (OUT / '准确度能力对照.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(json.dumps({'methods': summary['methods'], 'models': summary['models'], 'exclude_R08': summary['exclude_R08'],
                      'scoring_comparable': valid, 'mean_minutes': times, 'output': str(OUT)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

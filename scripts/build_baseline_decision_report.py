"""Summarize observed capability, model tradeoffs and resource costs; no API calls."""
import csv
import json
from pathlib import Path
from statistics import mean, median

from build_baseline_report import MODELS, table

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'artifacts/baseline-40-20260923'
AUDIT = ROOT / 'artifacts/baseline-accuracy-audit-20260923'
OUT = ROOT / 'artifacts/baseline-decision-report-20260924'
APP = ROOT / 'artifacts/baseline-report-20260923/app'
EXCEPTION_STATUSES = {'rejected', 'partial', 'unavailable'}
COMMON_RULES = {'GEN-003', 'TECH-001'}


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def price(low, high):
    return f'{low:.2f}' if abs(low - high) < 0.000001 else f'{low:.2f}–{high:.2f}'


def main():
    OUT.mkdir(exist_ok=True)
    rows = read_csv(BASE / 'results.csv')
    calls_all = read_csv(BASE / 'calls.csv')
    calls = [c for c in calls_all if c['interrupted_attempt'] == 'False']
    audit = json.loads((AUDIT / 'summary.json').read_text(encoding='utf-8'))
    totals = json.loads((BASE / 'totals.json').read_text(encoding='utf-8'))
    snapshot = json.loads((APP / 'src/data.json').read_text(encoding='utf-8'))
    assert len(rows) == len({r['run_id'] for r in rows}) == 40
    assert len(calls) + sum(c['interrupted_attempt'] == 'True' for c in calls_all) == len(calls_all)
    snapshot['buildStatus'] = 'updating'
    (APP / 'src/data.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    exceptions, checks_by_run = [], {}
    for r in rows:
        path = BASE / r['run_id'] / 'run.json'
        run = json.loads(path.read_text(encoding='utf-8'))
        checks_by_run[r['run_id']] = run
        count = 0
        for check in run['checks']:
            status = check.get('judgment', {}).get('status')
            if status in EXCEPTION_STATUSES:
                count += 1
                exceptions.append(dict(run_id=r['run_id'], case=r['case_id'], model=r['model'],
                                       method=r['method'], rule=check['rule']['id'], status=status,
                                       common_rule=check['rule']['id'] in COMMON_RULES,
                                       reason=check.get('reason', ''), source=str(path)))
        assert count == sum(int(r[k]) for k in ('judgment_rejected', 'judgment_partial', 'judgment_unavailable'))
    aggregate, models, gains, efficiency, critical = {}, [], [], [], []
    for method in ('direct', 'system'):
        group = [r for r in rows if r['method'] == method]
        cs = [c for c in calls if c['method'] == method]
        es = [e for e in exceptions if e['method'] == method]
        aggregate[method] = dict(comparable=sum(r['comparable'] == 'True' for r in group), n=len(group),
            exceptions=len(es), shared_exceptions=sum(e['common_rule'] for e in es),
            other_exceptions=sum(not e['common_rule'] for e in es),
            mean_minutes=mean(float(r['elapsed_seconds']) for r in group) / 60,
            cost_low=sum(float(c['estimated_cost_min']) for c in cs),
            cost_high=sum(float(c['estimated_cost_max']) for c in cs), calls=len(cs))
    for model, name in MODELS.items():
        groups = {}
        for method in ('direct', 'system'):
            group = [r for r in rows if r['model'] == model and r['method'] == method]
            cs = [c for c in calls if c['model'] == model and c['method'] == method]
            es = [e for e in exceptions if e['model'] == model and e['method'] == method]
            groups[method] = dict(comparable=sum(r['comparable'] == 'True' for r in group), exceptions=len(es),
                shared=sum(e['common_rule'] for e in es), other=sum(not e['common_rule'] for e in es),
                low=sum(float(c['estimated_cost_min']) for c in cs) / 5,
                high=sum(float(c['estimated_cost_max']) for c in cs) / 5,
                minutes=mean(float(r['elapsed_seconds']) for r in group) / 60,
                median=median(float(r['elapsed_seconds']) for r in group) / 60,
                maximum=max(float(r['elapsed_seconds']) for r in group) / 60, calls=len(cs) / 5)
        d, s = groups['direct'], groups['system']
        models.append(dict(model=name, version=model, comparable=f"{s['comparable']}/5",
            exceptions=s['exceptions'], shared=s['shared'], other=s['other'],
            spot=f"{audit['models'][model]['system']['exact_correct']}/10",
            cost=price(s['low'], s['high']), mean_minutes=round(s['minutes'], 2)))
        gains.append(dict(model=name, comparable=f"{d['comparable']}/5 → {s['comparable']}/5",
            exceptions=f"{d['exceptions']} → {s['exceptions']}",
            reduction=f"{100 * (d['exceptions']-s['exceptions']) / d['exceptions']:.1f}%",
            extra_cost=price(s['low']-d['low'], s['high']-d['high']),
            extra_minutes=round(s['minutes']-d['minutes'], 2)))
        efficiency.append(dict(model=name, cost=price(s['low'], s['high']),
            mean_minutes=round(s['minutes'], 2), median_minutes=round(s['median'], 2),
            max_minutes=round(s['maximum'], 2), calls=s['calls'],
            budget100=price(s['low']*100, s['high']*100),
            cost_low=s['low'], cost_high=s['high']))
        for method in ('direct', 'system'):
            subset = [r for r in rows if r['model'] == model and r['method'] == method]
            run5 = checks_by_run[next(r['run_id'] for r in subset if r['case_id'] == 'R05')]
            run9 = checks_by_run[next(r['run_id'] for r in subset if r['case_id'] == 'R09')]
            check5 = next(c for c in run5['checks'] if c['rule']['id'] == 'TUR-CON-002')
            check9 = next(c for c in run9['checks'] if c['rule']['id'] == 'FSR-CAP49')
            critical.append(dict(model=name, method='直接' if method == 'direct' else '系统',
                investment='检出120万元矛盾' if check5['result'] == 'fail' and check5.get('judgment', {}).get('status') == 'completed' else '引用核验失败，待确认',
                scope=f"上限{run9['quality_score']['upper_bound']}分" + ('，范围未决' if run9['quality_score'].get('cap_unresolved') else ''),
                scope_result=check9['result'], investment_result=check5['result']))
    d, s = aggregate['direct'], aggregate['system']
    assert (d['exceptions'], s['exceptions'], d['shared_exceptions'], s['shared_exceptions']) == (82, 45, 38, 38)
    capability = [
        dict(metric='评分可比率', direct='17/20（85%）', system='20/20（100%）', meaning='提高15个百分点；评分未决减少'),
        dict(metric='处理异常条目（越少越好）', direct='82', system='45', meaning='净减少37条，下降45.1%'),
        dict(metric='异常／报告（越少越好）', direct='4.10', system='2.25', meaning='每份平均少1.85条处理异常'),
        dict(metric='两条共性规则之外的异常', direct='44', system='7', meaning='下降84.1%；事后定位，不能替代全量口径'),
        dict(metric='三态抽核一致率', direct='38/40（95%）', system='38/40（95%）', meaning='未显示整体准确度提升'),
        dict(metric='抽核中无依据给分', direct='2次', system='1次', meaning='少1次；另出现1次缺证据误判不符合'),
    ]
    definitions = [
        dict(metric='评分可比率', definition='comparable=True的运行数／该组总运行数', meaning='衡量评分是否有处理或范围未决，不衡量正确性'),
        dict(metric='处理异常条目', definition='每条规则的judgment.status为rejected、partial或unavailable时计1条', meaning='同一报告同一规则最多计1条；不计正常业务待确认'),
        dict(metric='三态抽核一致率', definition='符合／不符合／待确认与参考标签完全相同的条数／抽核条数', meaning='方法合计分母40；单模型单方法分母10；助手事后标签'),
        dict(metric='关键案例判断', definition='R05投资矛盾与R09专项篇边界逐一回看原文和输出', meaning='事后案例检查；单列展示，不拼成综合准确率'),
        dict(metric='单份成本', definition='完成尝试逐调用估算费用总和／5份报告', meaning='按本轮价格快照与用量；不含人为暂停重跑'),
        dict(metric='单份耗时', definition='完成尝试elapsed_seconds／60，展示均值、中位数和最大值', meaning='包含本轮并发与排队影响；每模型仅5份，不估计P95'),
        dict(metric='百份预算', definition='未四舍五入的单份成本×100', meaning='假设报告组合、模型价格和用量结构不变；非实测账单'),
    ]
    columns = {
        'capability': [('metric','指标'),('direct','直接评审'),('system','系统评审'),('meaning','解释')],
        'modelability': [('model','模型'),('comparable','评分可比'),('exceptions','异常总数'),('shared','共性规则异常'),('other','其余异常'),('spot','抽核一致')],
        'gains': [('model','模型'),('comparable','评分可比：直接→系统'),('exceptions','异常：直接→系统'),('reduction','异常降幅'),('extra_cost','增量元/份'),('extra_minutes','增量分钟/份')],
        'efficiency': [('model','模型'),('cost','元/份'),('mean_minutes','平均分钟'),('median_minutes','中位分钟'),('max_minutes','最慢分钟'),('budget100','百份预算/元')],
        'critical': [('model','模型'),('method','方法'),('investment','R05投资矛盾'),('scope','R09专项篇上限')],
        'definitions': [('metric','指标'),('definition','计算口径'),('meaning','解释边界')],
    }
    summary = (
        '**系统流程的主要收益是减少处理异常、稳定形成评分。** 40次实测中，系统组评分可比率为100%，比直接组高15个百分点；处理异常由82条降至45条，下降45.1%。这支持系统流程的工程价值，不等于准确率提升。\n\n'
        '**本轮综合成本、耗时和处理异常，DeepSeek可作为常规系统评审的优先候选，Qwen适合预算优先的批量初评。** '
        'DeepSeek约0.52–1.04元/份、5.44分钟/份；Qwen约0.39元/份、7.74分钟/份。两者均5/5评分可比。'
        'Qwen尚有投资矛盾引用核验失败，需对关键金额项复核。\n\n'
        '**高价模型尚未体现相应收益。** Kimi约9.97元/份、9.68分钟/份；与Qwen相比成本约25.8倍。'
        '两种方法在既有小范围三态抽核中均为95%一致；没有证据支持用更高报告均分或更高API单价替代能力判断。'
    )
    new_sections = [
        ('summary','结论摘要',summary),
        ('capability','系统减少了处理异常，并提高评分可比率',table(capability, columns['capability']) + '\n\n'
         '处理异常指输出被拒收、判据处理不完整或判断不可用，不包含“报告确实缺材料”造成的正常待确认。直接组异常由78条拒收、4条部分完成构成；系统组为26条拒收、19条不可用。只展示拒收下降会遗漏不可用状态，本报告使用三种状态合计。\n\n'
         'GEN-003、TECH-001两条通用规则在两种方法中均贡献38条异常；系统剩余异常的84.4%集中在这两条规则。剔除它们后的44→7仅用于定位问题，主结论仍使用82→45的全量统计。净减少37条不意味着已逐条证明37次修复成功，也不是人工工时实测。'),
        ('modelability','四种模型在相同系统流程中的表现',table(models, columns['modelability']) + '\n\n'
         '每模型均测试相同5份报告。DeepSeek、Qwen、Kimi、智谱分别使用deepseek-v4.1-flash、qwen3.8-flash、kimi-k3、glm-5.2。异常为5份报告合计。\n\n'
         'DeepSeek全量处理异常最少（9条）；Qwen仍有6条发生在上述两条共性规则之外，需关注引用与金额映射。Kimi、智谱的10条异常全部集中在两条共性规则，其他规则未出现这三类处理状态，但不表示业务判断全部正确。\n\n'
         '“抽核一致”仅指5份报告的基线证据和性能目标两项判据，共10个单元。参考标签由助手事后制定，未经独立专家盲审；R08解释口径存在争议，去除R08后各模型两种方法均8/8一致，因此不用该列做综合准确率排名。'),
        ('critical','金额矛盾与材料范围仍需要单独检查',table(critical, columns['critical']) + '\n\n'
         '**R05：设备费120万元，总投资填0万元。** 正确做法是指出同一口径的120万元矛盾。直接组四模型均检出，系统组DeepSeek、Kimi、智谱检出，Qwen因引用核验失败停在待确认。两组共用末端程序核算，不能把该能力全部归因于系统新增流程。\n\n'
         '**R09：输入标题明确为“科技创新篇”，不是完整可研。** 按冻结的FSR-CAP49判据，材料范围应触发49分上限。系统组DeepSeek、Qwen、Kimi触发，智谱误将专项篇解释为满足“完整可研”，保留92分上限；其实际32分未触及上限，故总分未暴露这处语义错误。这是为何只看评分可比率仍不够。\n\n'
         '这两个案例是事后选取的、可直接回看原文的诊断项，未并入前述抽核一致率，也不能据此估计全部关键缺陷的检出率。'),
        ('gains','系统流程对各模型的改善与增量开销',table(gains, columns['gains']) + '\n\n'
         'Qwen在评分可比性上的改善最大：3/5提升至5/5，异常34→16；DeepSeek为4/5→5/5，异常12→9。Kimi、智谱直接组本已5/5评分可比，系统收益主要体现在其他规则的处理异常减少。\n\n'
         '成本与耗时增量按同模型、相同5份报告计算，包括未能形成可比分数的直接运行，不将这些运行删除或视为免费。'),
        ('efficiency','Qwen成本最低，DeepSeek返回最快',table(efficiency, columns['efficiency']) + '\n\n'
         '金额为人民币，依据2026-09-23本轮接口价格快照和返回用量估算，不是实际账单，也不是当前重新报价。DeepSeek保留峰谷价格区间。每模型只有5份报告，展示最慢样本而不推算高分位延迟。百份预算是同样报告组合、价格和调用结构下的线性情景。\n\n'
         '系统组Qwen比DeepSeek便宜约25.4%–62.7%；DeepSeek比Qwen平均快29.7%。Kimi价格约为Qwen的25.8倍，平均耗时也更长；现有抽核未证明其准确性优势。智谱平均5.86分钟，接近DeepSeek，但每份约3.64元且存在专项篇范围误判。\n\n'
         f"本轮20次系统评审合计{price(s['cost_low'],s['cost_high'])}元，20次直接评审{price(d['cost_low'],d['cost_high'])}元。系统整体API开销约为直接的3.42倍、平均耗时为2.56倍，经济性来自选择合适模型和减少后续处理负担，不能表述为系统比直接调用更便宜或更快。\n\n"
         f"包含人为暂停重跑的57次调用后，全轮费用估算为{price(totals['estimated_cost_min'],totals['estimated_cost_max'])}元；这57次不计入上述模型效率比较。未实测人工复核时间、图表识别成本及基础设施开销，因此不计算人力节省金额或总投资回报。"),
        ('decision','按使用场景选择模型与流程',
         '- **常规系统评审：优先试用DeepSeek。** 本轮平均5.44分钟，0.52–1.04元/份，全量异常最少，两个关键案例判断也符合原文。定位为本轮实测下的优先候选，不是跨专业通用最优模型。\n'
         '- **预算优先的批量初评：优先考虑Qwen。** 按本轮用量百份约38.72元；金额矛盾与引用核验未完成的项目需复核，不能仅因5/5评分可比直接出具最终结论。\n'
         '- **快速浏览或材料初筛：保留直接评审。** 平均2.81分钟即可返回，适合先定位问题；需要完整分项和稳定汇总时再使用系统流程。这种分层调度尚未实测，不能提前宣称节省比例。\n'
         '- **Kimi、智谱：保留为差异复核候选。** 当前费用与已证实能力不足以支持它们作为默认配置；使用前先确认是否在实际争议项上带来增益。\n\n'
         '下一轮优先复用现有输出：明确GEN-003、TECH-001判据映射；复核Qwen金额引用失败和智谱专项篇反向理解；由专业人员裁定R08边界与关键缺陷标签。完成这些工作后，再用固定标签评估准确率和关键缺陷漏检率。'),
        ('definitions','指标定义与可复现口径',table(definitions, columns['definitions']) + '\n\n'
         '测试为5报告×4模型×2方法，合计40次，每组合仅1次。报告得分是被评材料的分数，不能作为模型能力分数；不对现有指标任意加权生成综合排名。此报告编制于2026-09-24，实测证据截止2026-09-23。原始评分矩阵、原文解释、方法配对和完整资源表保留在后文。'),
    ]
    new_ids = {i for i, _, _ in new_sections}
    retained = [section for section in snapshot['reportSections'] if section['id'] not in new_ids]
    snapshot['reportSections'] = [dict(id=i, title=t, body=b) for i, t, b in new_sections] + retained
    snapshot['title'] = '系统评审基线：能力、模型选择与成本时效'
    snapshot['report']['asOf'] = '2026-09-23'
    datasets = {'capability': capability, 'modelability': models, 'gains': gains, 'efficiency': efficiency,
                'critical': critical, 'definitions': definitions}
    files = [BASE / 'results.csv', BASE / 'calls.csv', BASE / 'prices.json', AUDIT / 'summary.json',
             AUDIT / 'reference-labels.json', Path(__file__)]
    files += [BASE / r['run_id'] / 'run.json' for r in rows]
    for query_id, data in datasets.items():
        definition = '按相同5报告和4模型分组；异常为rejected/partial/unavailable，正常业务待确认不计异常；费用、耗时仅最终完成尝试。抽核为助手事后参考，百份预算为线性情景。'
        snapshot['queries'][query_id] = dict(rows=data, source=dict(label='2026-09-23首轮实测及事后抽核',
            files=[str(f) for f in files], executedAt='2026-09-24',
            metricDefinitions=[dict(label='统计定义', definition=definition,
                componentIds=[f'baseline-{query_id}', f'baseline-{query_id}-table'])],
            evidenceFlow=[dict(title='冻结记录', detail='40次运行及1275次调用；模型对照排除57次人为暂停调用。'),
                          dict(title='复现统计', detail='运行scripts/build_baseline_decision_report.py；按脚本逐项汇总，不修改原始评审。')]),
            methods=[dict(language='text', code='.\\.runtime\\Scripts\\python.exe scripts/build_baseline_decision_report.py')])
    for filename, data in [('model-comparison.csv', models), ('efficiency.csv', efficiency), ('processing-exceptions.csv', exceptions)]:
        with (OUT / filename).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    (OUT / 'metrics.json').write_text(json.dumps(dict(aggregate=aggregate, **datasets), ensure_ascii=False, indent=2), encoding='utf-8')
    md = '# 系统评审基线测试报告：能力、模型选择与成本时效\n\n'
    md += '\n\n'.join('## ' + section['title'] + '\n\n' + section['body'] for section in snapshot['reportSections'])
    md += '\n\n## 复现与数据文件\n\n- 原始记录：`artifacts/baseline-40-20260923/`。\n- 事后抽核：`artifacts/baseline-accuracy-audit-20260923/`。\n- 本报告明细：`model-comparison.csv`、`efficiency.csv`、`processing-exceptions.csv`、`metrics.json`。\n- 项目根目录运行：`.\\.runtime\\Scripts\\python.exe scripts/build_baseline_decision_report.py`。\n'
    (OUT / '系统评审基线测试报告.md').write_text(md, encoding='utf-8')
    snapshot['buildStatus'] = 'complete'
    (OUT / 'reviewed.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    (APP / 'src/data.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(runs=len(rows), completed_calls=len(calls), aggregate=aggregate,
                         report=str(OUT / '系统评审基线测试报告.md')), ensure_ascii=False))


if __name__ == '__main__':
    main()

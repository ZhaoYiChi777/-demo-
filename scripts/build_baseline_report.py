"""Build a source-backed first-round baseline report without changing experiment data."""
import csv
import json
from pathlib import Path
from statistics import mean
from decimal import Decimal, ROUND_HALF_UP

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'artifacts/baseline-40-20260923'
OUT = ROOT / 'artifacts/baseline-report-20260923'
MODELS = {'deepseek-v4.1-flash': 'DeepSeek', 'qwen3.8-flash': 'Qwen', 'kimi-k3': 'Kimi', 'glm-5.2': '智谱'}
CASES = {'R03': '主汽门严密性治理', 'R05': '抽汽系统优化', 'R07': '抗燃油系统改造', 'R08': '高旁系统改造', 'R09': '深度调峰科技创新篇'}


def read_csv(name):
    with (SOURCE / name).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def table(rows, columns):
    return '\n'.join(['| ' + ' | '.join(label for _, label in columns) + ' |',
                      '| ' + ' | '.join('---' for _ in columns) + ' |',
                      *['| ' + ' | '.join(str(row[key]) for key, _ in columns) + ' |' for row in rows]])


def main():
    OUT.mkdir(exist_ok=True)
    rows, calls = read_csv('results.csv'), read_csv('calls.csv')
    totals = json.loads((SOURCE / 'totals.json').read_text(encoding='utf-8'))
    assert len(rows) == len({r['run_id'] for r in rows}) == 40
    index = {(r['case_id'], r['model'], r['method']): r for r in rows}
    method_rows, model_rows, paired_rows, matrix = [], [], [], []
    for method, label in [('direct', '直接评审'), ('system', '系统评审')]:
        selected = [r for r in rows if r['method'] == method]
        completed_calls = [c for c in calls if c['method'] == method and c['interrupted_attempt'] == 'False']
        good = sum(r['comparable'] == 'True' for r in selected)
        cost_low = sum(float(c['estimated_cost_min']) for c in completed_calls)
        cost_high = sum(float(c['estimated_cost_max']) for c in completed_calls)
        method_rows.append(dict(method=label, completed='20/20', comparable=f'{good}/20（{good*5}%）',
            fully_processed=f"{sum(r['judgment_status']=='complete' for r in selected)}/20",
            mean_minutes=round(mean(float(r['elapsed_seconds']) for r in selected)/60, 2),
            api_calls=len(completed_calls), cost=f'{cost_low:.2f}–{cost_high:.2f}'))
    for model, name in MODELS.items():
        valid_cases = [case for case in CASES if all(index[case, model, m]['comparable'] == 'True' for m in ('direct', 'system'))]
        direct_mean = sum(Decimal(index[c, model, 'direct']['score']) for c in valid_cases)/len(valid_cases)
        system_mean = sum(Decimal(index[c, model, 'system']['score']) for c in valid_cases)/len(valid_cases)
        two = lambda value: float(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        paired_rows.append(dict(model=name, cases='、'.join(valid_cases), n=len(valid_cases),
            direct=two(direct_mean), system=two(system_mean), delta=two(system_mean-direct_mean)))
        for method, label in [('direct', '直接'), ('system', '系统')]:
            rs=[r for r in rows if r['model']==model and r['method']==method]
            cs=[c for c in calls if c['model']==model and c['method']==method and c['interrupted_attempt']=='False']
            low=sum(float(c['estimated_cost_min']) for c in cs); high=sum(float(c['estimated_cost_max']) for c in cs)
            model_rows.append(dict(model=name, method=label, comparable=f"{sum(r['comparable']=='True' for r in rs)}/5",
                mean_minutes=round(mean(float(r['elapsed_seconds']) for r in rs)/60, 2),
                api_calls=len(cs), cost=f'{low:.2f}–{high:.2f}', cost_per_report=f'{low/5:.2f}–{high/5:.2f}'))
    for case, name in CASES.items():
        item=dict(case=case, report=name)
        for model, model_name in MODELS.items():
            values=[]
            for method in ('direct', 'system'):
                r=index[case,model,method]
                values.append(f"{float(r['score']):.1f}"+(' *' if r['comparable']!='True' else ''))
            item[model_name]=' → '.join(values)
        matrix.append(item)
    evidence = [
        dict(case='R03', fact='原文列明本厂多次检修及阀座硬度177–259 HB、223–249 HB，目标不低于380 HB，并列严密性试验；费用190+8+2+60=260万元。', interpretation='具备本厂数据、目标及费用明细，适合检查数值与引用是否对应；高分具有文本依据，但不是专家合格认定。'),
        dict(case='R05', fact='原文列明关闭时间1102–2410 ms，目标小于1000 ms；设备明细6×20=120万元，但工程总投资填0万元。', interpretation='技术目标清晰而投资表存在矛盾。Kimi系统记录TUR-CON-002不符合、需大量修改；92分不能解释为报告可直接通过。'),
        dict(case='R07', fact='原文包含在线活动试验、改造后的匹配试验、密封材料和可靠性改善描述；详细性能参数仍写需投标方提供。', interpretation='Kimi系统比直接低6分，集中于验证途径2.5分和材料/长期可靠性依据3.5分。应人工核对证据充分性的边界，不能直接认定严格判定更准确。'),
        dict(case='R08', fact='原文称本厂高旁目前正常、无缺陷，问题主要引自兄弟电厂；给出泄漏对热耗的影响，但具体性能参数交设计院。', interpretation='Kimi直接→系统84.2→74.5。差异来自本厂证据、量化验收目标及施工风险。外厂案例不等于本厂缺陷，影响估算不等于性能保证值。'),
        dict(case='R09', fact='材料是科技创新篇，主要说明技术原理、拟解决难点、2026年计划及1150万元预算，并非完整可研。', interpretation='应单独解释专项篇边界。DeepSeek和Qwen直接组封顶范围未确定；智谱系统组上限为92，其余三家系统组为49，仍有范围判定分歧；智谱实际32分未触及任一上限。'),
    ]
    stats = dict(methods=method_rows, models=model_rows, paired=paired_rows, matrix=matrix, evidence=evidence, totals=totals)
    (OUT/'analysis.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = ('本轮完成5份报告×4种模型×2种方法，共40组实际评审。系统组评分可比率为20/20（100%），直接组为17/20（85%），提高15个百分点。'
        '但全部业务判据处理完成的运行仅系统组1/20、直接组0/20，因此该改进只说明评分输出更完整，不能作为准确率提升的证明。\n\n'
        '系统组平均7.18分钟/报告，直接组2.81分钟，约为2.56倍。排除人为暂停造成的57次中断调用后，系统组921次API请求、直接组297次。'
        '含中断记录的全轮目录价格估算为94.40–97.78元。\n\n'
        '报告得分并非模型成绩。本轮各组合只运行一次，没有专家标准答案；本报告不给出准确率、F1或“最佳模型”的结论。')
    methods = ('测试日期为2026-09-23；模型版本为deepseek-v4.1-flash、qwen3.8-flash、kimi-k3、glm-5.2。'
        '直接组按固定规则批次读取完整报告、一轮生成判断；系统组使用检索、补检索、逐项复核、输出修复与程序核算。'
        '两组共用同一报告解析文本、评分分支、86条业务规则、14条评分/封顶规则和末端引用核验与计分器。'
        '因此这是“完整系统流程相对固定规则直接评审”的对照，不是去掉所有程序核验的纯模型对照。\n\n'
        '评分采用共性70分+类型分支30分、通常最高92分；专项篇可触发49分上限。R03/R05/R07/R08使用安全可靠性分支，R09使用科技创新分支。'
        '固定调度种子20260923；每组合1次，2份报告并发，每份最多2批并发。模型生成沿用服务默认温度，未固定生成种子。'
        '本轮不新增图表视觉识别，结论仅针对已解析文本和表格文字。')
    limits = ('评分可比：当前评分器没有阻断计分的处理未决或封顶范围未决；不等于全部业务判据完成，更不等于判断正确。'
        '三组不可比分数为Qwen-R03直接、DeepSeek-R09直接、Qwen-R09直接，保留显示但不进入有效配对均值。'
        '有效配对共17对。不同模型的有效样本集合不同，不用下表均值跨模型排名。\n\n'
        '费用按本轮接口目录价格和返回token用量估算；不是账单。方法与模型资源表只统计最终完成尝试，避免暂停重跑混入方法开销；全轮总费用包含中断调用。'
        '耗时为每份报告的实际墙钟时间，包含该配置下排队与并发影响，未计暂停前尝试，也不代表单请求延迟。')
    actions = ('基于本轮已测资源开销，成本优先可将Qwen系统组列为后续候选（约0.39元/报告、7.74分钟），'
        '时延优先可将DeepSeek系统组列为候选（约0.52–1.04元/报告、5.44分钟）；这是资源选择建议，不是准确率排名。'
        'Kimi系统组约9.97元/报告，本轮未建立足以支撑该开销的准确性优势。\n\n'
        '下一步应先由人工核对现有分歧项，重点是R07验证/材料依据、R08本厂证据/验收条件和R09专项篇范围；形成固定标签后再计算准确率与漏检率。'
        '不要为了提高均分修改原始结果。本轮也不足以证明跨专业泛化或重复稳定性。')
    sections = [
        ('summary','主要结论',summary),
        ('methods','测试设计',methods),
        ('scores','逐报告评分：直接 → 系统',table(matrix,[('case','报告'),('report','内容'),*[(n,n) for n in MODELS.values()]])+'\n\n星号（*）表示评分不可比，未当作0分或删除。所有分数均保留原始输出。'),
        ('completion','处理完成度与整体开销',table(method_rows,[('method','方法'),('completed','生成记录'),('comparable','评分可比'),('fully_processed','业务判据处理全完成'),('mean_minutes','分钟/报告'),('api_calls','API调用'),('cost','20组费用/元')])+'\n\n'+limits),
        ('pairs','相同报告上的方法配对',table(paired_rows,[('model','模型'),('cases','有效报告'),('n','配对数'),('direct','直接均分'),('system','系统均分'),('delta','系统减直接')])+'\n\nDeepSeek与Qwen的均值用两种方法共同可比的报告计算，不能直接相减先前不同分母的全组均值。分差代表评分变化，不代表质量改善。'),
        ('evidence','结合报告原文解释结果', '\n\n'.join(f"### {e['case']} {CASES[e['case']]}\n\n原文事实：{e['fact']}\n\n结果解释：{e['interpretation']}" for e in evidence)+'\n\n以上是对本轮原文和输出的定性核对，不是独立专家金标准；原报告引用的规范不在此重新作时效认定。'),
        ('resources','四种模型的资源开销',table(model_rows,[('model','模型'),('method','方法'),('comparable','评分可比'),('mean_minutes','分钟/报告'),('api_calls','5组调用'),('cost','5组费用/元'),('cost_per_report','元/报告')])+'\n\n全轮共1,275次已保存调用，接口错误0次，用量缺失0次；其中最终完成尝试1,218次、中断尝试57次。所有返回型号均与配置一致。'),
        ('decision','本轮能支持的选择与下一步',actions),
    ]
    md='# 首轮基线测试结果：系统评分更完整，准确性仍需人工对照\n\n'+'\n\n'.join(f'## {title}\n\n{body}' for _,title,body in sections)
    md+='\n\n## 复核来源\n\n- 原始实验：../baseline-40-20260923/\n- 40组明细：results.csv；逐调用：calls.csv；配对：paired-results.csv。\n- 输入与规则：inputs.json、config.json、plan.json；每组run.json和call-*.json。\n- 本报告复现：从项目根目录运行 `.\\.runtime\\Scripts\\python.exe scripts/build_baseline_report.py`。\n'
    (OUT/'首轮基线测试结果.md').write_text(md,encoding='utf-8')
    def query(data, files, definition):
        return {'rows':data,'source':{'label':'本地40组实测记录','files':[str(SOURCE/f) for f in files],'executedAt':'2026-09-23T08:04:30Z',
            'evidenceFlow':[{'title':'读取冻结实测记录','detail':', '.join(files)},{'title':'汇总口径','detail':definition}],
            'metricDefinitions':[{'label':'统计口径','definition':definition}]},
            'methods':[{'language':'text','code':definition}]}
    snapshot={'surface':'report','title':'40次基线实测：评分完整度与资源开销','generatedAt':'2026-09-23T08:04:30Z',
        'status':'observed','buildStatus':'creating','filters':[],'report':{'asOf':'2026-09-23'},'queries':{
        'scores':query(matrix,['results.csv'],'每行一份报告，每单元格为同模型直接→系统原始评分；*标记不可比。'),
        'methods':query(method_rows,['results.csv','calls.csv'],'每方法20组；评分可比与业务判据全完成分开统计；时延/费用排除中断尝试。'),
        'pairs':query(paired_rows,['results.csv'],'同模型、同报告、两种方法均可比才纳入配对均值；分母为4、3、5、5，不跨模型排名。'),
        'resources':query(model_rows,['results.csv','calls.csv','prices.json'],'每模型每方法5组；费用按逐调用input/output/cache用量×目录价格；排除中断尝试。'),
        'evidence':query(evidence,[f'{c}-system-3-01/run.json' for c in CASES],'原文事实与本次定性解释分列，不作专家标签。'),
        'runs':query(rows,['results.csv'],'40个唯一报告×模型×方法组合；保留处理不完整结果。')},
        'reportSections':[{'id':i,'title':t,'body':b} for i,t,b in sections]}
    (OUT/'reviewed.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'reports':len(matrix),'runs':len(rows),'pairs':sum(x['n'] for x in paired_rows),'output':str(OUT)},ensure_ascii=False))


if __name__ == '__main__':
    main()

"""Tabulate measured pilot results without adjusting any model score."""
import csv
import json
from pathlib import Path
import sys


def main():
    root = Path(sys.argv[1]).resolve()
    rows = json.loads((root / 'summary.json').read_text(encoding='utf-8'))
    prices = {m['model']: m['prices'][0]['prices'] for m in json.loads((root / 'prices.json').read_text(encoding='utf-8'))}
    details, dimensions = [], []
    for row in rows:
        folder = root / (row['case_id'] + '-' + row['model'])
        calls = [json.loads(p.read_text(encoding='utf-8')) for p in folder.glob('call-*.json')]
        payloads = [json.loads(c['request']['messages'][1]['content']) for c in calls]
        row['output_repair_calls'] = sum('validation_error' in p for p in payloads)
        model_prices = prices[row['model']]
        costs = []
        for band in sorted({p['time_band'] for p in model_prices if p['type'] == 'input_token'}):
            rates = {p['type']: float(p['price']) for p in model_prices if p['time_band'] == band}
            costs.append(((row['input_tokens'] - row['cached_input_tokens']) * rates['input_token']
                          + row['cached_input_tokens'] * rates['input_token_cache']
                          + row['output_tokens'] * rates['output_token']) / 1_000_000)
        row.update(cost_cny_low=round(min(costs), 6), cost_cny_high=round(max(costs), 6),
                   usage_complete=row['usage_calls'] == row['api_calls'])
        path = folder / 'run.json'
        if not path.exists():
            continue
        run = json.loads(path.read_text(encoding='utf-8'))
        score = run['quality_score']
        row.update(lower_bound=score.get('lower_bound'), scoring_status=score.get('scoring_status'),
                   cap_unresolved=score.get('cap_unresolved'),
                   unresolved_points=round(sum(d['unresolved'] for d in score['dimensions']), 2))
        for d in score['dimensions']:
            dimensions.append(dict(case_id=row['case_id'], model=row['model'], dimension=d.get('id'),
                                   name=d.get('name'), weight=d.get('effective_weight'),
                                   contribution=d['contribution'], unresolved=d['unresolved'],
                                   excluded=d['excluded'], **{k: d.get(k) for k in ('pass', 'fail', 'pending', 'total')}))
        for check in run['checks']:
            details.append(dict(case_id=row['case_id'], model=row['model'], rule_id=check['id'],
                                rule_name=check['rule'].get('name'), result=check['result'],
                                judgment_status=check.get('judgment', {}).get('status'), reason=check['reason']))
    for name, data in [('results', rows), ('rule_results', details), ('dimension_results', dimensions)]:
        fields = list(dict.fromkeys(k for row in data for k in row))
        with (root / (name + '.csv')).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data)
    (root / 'results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 三份报告、两种模型首轮实测', '',
             '运行日期：2026-09-23。使用现有系统完整流程，每个组合运行一次，共6次报告评审；不是6次API请求。', '',
             '## 分数及开销', '',
             '| 报告 | 模型 | 暂计分 | 判定状态 | 被拒结果数 | 未决分值 | 待确认规则/全部规则 | 用时（分钟） | API请求 | 修复请求 | 估算费用（元） |',
             '| --- | --- | ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |']
    for r in rows:
        lines.append(f"| {r['case_id']} {r['report']} | {r['model']} | {r.get('score', '未生成')} | {r.get('judgment_status', '未生成')} | {r.get('judgment_rejected', '—')} | {r.get('unresolved_points', '—')} | {r.get('pending', '—')}/{r.get('total', '—')} | {r['elapsed_seconds']/60:.1f} | {r['api_calls']} | {r['output_repair_calls']} | {r['cost_cny_low']:.3f}–{r['cost_cny_high']:.3f} |")
    low, high = sum(r['cost_cny_low'] for r in rows), sum(r['cost_cny_high'] for r in rows)
    lines += ['', f"合计：{sum(r['api_calls'] for r in rows)}次API请求；按返回token用量及当日接口价目估算人民币{low:.3f}–{high:.3f}元。DeepSeek按峰谷价格给区间，最终扣费以账单为准。",
              f"用量返回覆盖：{sum(r['usage_calls'] for r in rows)}/{sum(r['api_calls'] for r in rows)}次请求；失败请求数：{sum(r['call_errors'] for r in rows)}。没有返回用量的请求未包含在费用内。", '',
              '## 如何解释', '',
              '- 评分沿用fsr-70-30-v4，共性70＋分支30，最终最高92分。分数是模型通过系统给报告的暂计成熟度分，不是模型准确率。',
              '- 未决分值是尚待确认的评分权重，已经按评分器有效权重汇总；不另加到实测分数中。规则待确认数量与评分未决分值是不同指标。',
              '- 判定状态partial表示部分模型结果未通过程序核验；这会使部分分项不能计分，不能将对应低分全部归因于报告质量。complete仅表示流程判定完成，仍可能有真实缺证或后处理标记的待确认事项。',
              '- 本轮R09为科技创新专项篇章：DeepSeek触发49分上限；Qwen的完整可研范围条件仍未确认，因此其upper_bound=92不能解释为已认可该材料可按完整可研评分。具体状态见results.csv的cap_unresolved列。',
              '- 仅做固定样本选择、统一汇总和实际用量计费，没有改分、补造其余七份报告或挑选较好运行。',
              '- 每个组合仅运行一次，无专家标准答案；不能用高分直接宣布模型更准，也不能给出重复稳定性或系统相对直接评审的增益。',
              '- 原始规则及引用见各组合run.json，模型请求、回答和用量见call-*.json；固定输入为inputs.json，当时评审代码位于source/。', '',
              '复现命令（会重新调用付费模型，使用新的输出目录）：', '',
              '```powershell', '.\\.runtime\\Scripts\\python.exe scripts/run_small_baseline.py --output artifacts/small-baseline-rerun',
              '.\\.runtime\\Scripts\\python.exe scripts/summarize_small_baseline.py artifacts/small-baseline-rerun', '```', '']
    (root / '结果说明.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

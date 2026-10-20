"""Export observed report results and all billed attempts; no score adjustment."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
from statistics import mean


def write_json(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def write_csv(path,rows):
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def estimate(usage,prices):
    if usage is None:return None,None
    options=defaultdict(list)
    for group in prices:
        for item in group.get('prices',[]):
            if item['type'] in ('input_token','output_token','input_token_cache'):
                options[item['type']].append(float(item['price']))
    if not options['input_token'] or not options['output_token']:return None,None
    input_tokens=usage.get('prompt_tokens',0)
    cached=(usage.get('prompt_tokens_details') or {}).get('cached_tokens',0)
    cached=max(0,min(input_tokens,cached))
    output=usage.get('completion_tokens',0)
    cache_rates=options['input_token_cache'] or options['input_token']
    return tuple(round(((input_tokens-cached)*fn(options['input_token'])+cached*fn(cache_rates)+output*fn(options['output_token']))/1e6,8) for fn in (min,max))


def call_rows(folder,plan):
    prices={r['model']:r['prices'] for r in json.loads((folder/'prices.json').read_text(encoding='utf-8'))}
    rows=[]
    for task in plan:
        dirs=[folder/task['run_id'],*folder.glob(task['run_id']+'-interrupted-*')]
        for directory in dirs:
            for path in sorted(directory.glob('call-*.json')):
                record=json.loads(path.read_text(encoding='utf-8'));usage=record.get('usage')
                payload=json.loads(record['request']['messages'][-1]['content'])
                phase=('output_repair' if payload.get('validation_error') else 'audit' if payload.get('draft_judgments') else
                       'judgment' if payload.get('rules') and 'evidence_ids' in payload['rules'][0] else 'retrieval')
                low,high=estimate(usage,prices.get(task['model'],[]))
                rows.append(dict(**task,attempt=directory.name,call=path.name,stage=phase,
                    returned_model=record.get('returned_model'),request_id=record.get('request_id'),
                    finish_reason=record.get('finish_reason'),error=record.get('error'),
                    elapsed_seconds=record.get('elapsed_seconds'),has_usage=usage is not None,
                    input_tokens=usage.get('prompt_tokens') if usage else None,
                    output_tokens=usage.get('completion_tokens') if usage else None,
                    cached_input_tokens=(usage.get('prompt_tokens_details') or {}).get('cached_tokens',0) if usage else None,
                    estimated_cost_min=low,estimated_cost_max=high,
                    interrupted_attempt=directory.name!=task['run_id']))
    return rows


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();folder=args.output.resolve()
    plan=json.loads((folder/'plan.json').read_text(encoding='utf-8'))
    calls=call_rows(folder,plan);by_run=defaultdict(list)
    for c in calls:by_run[c['run_id']].append(c)
    rows=[];predictions=[];errors=[]
    for task in plan:
        target=folder/task['run_id'];summary=target/'summary.json'
        if not summary.exists():continue
        row=json.loads(summary.read_text(encoding='utf-8'));records=by_run[task['run_id']]
        row.update(all_attempt_api_calls=len(records),calls_without_usage=sum(not c['has_usage'] for c in records),
                   estimated_cost_min=round(sum(c['estimated_cost_min'] or 0 for c in records),6),
                   estimated_cost_max=round(sum(c['estimated_cost_max'] or 0 for c in records),6),
                   interrupted_attempt_calls=sum(c['interrupted_attempt'] for c in records))
        rows.append(row)
        if (target/'run.json').exists():
            run=json.loads((target/'run.json').read_text(encoding='utf-8'))
            for check in run['checks']:
                judgment=check.get('judgment',{})
                predictions.append(dict(**task,unit='rule',rule_id=check['id'],result=check['result'],
                    status=judgment.get('status','deterministic'),applicability=judgment.get('applicability'),reason=check['reason']))
                if judgment.get('status') in ('rejected','unavailable') and not judgment.get('criteria'):
                    errors.append(dict(**task,unit='rule',rule_id=check['id'],criterion_id=None,status=judgment['status'],reason=judgment.get('reason')))
                for criterion in judgment.get('criteria',[]):
                    predictions.append(dict(**task,unit='criterion',rule_id=check['id'],**criterion))
                    if criterion.get('processing_error'):
                        errors.append(dict(**task,unit='criterion',rule_id=check['id'],criterion_id=criterion.get('criterion_id'),status='processing_error',reason=criterion['processing_error']))
    rows.sort(key=lambda r:(r['case_id'],r['model'],r['method']))
    write_csv(folder/'results.csv',rows);write_csv(folder/'calls.csv',calls);write_csv(folder/'processing-errors.csv',errors)
    with (folder/'predictions.jsonl').open('w',encoding='utf-8') as f:
        for p in predictions:f.write(json.dumps(p,ensure_ascii=False)+'\n')
    groups=[]
    for model in dict.fromkeys(t['model'] for t in plan):
        for method in ('direct','system'):
            selected=[r for r in rows if r['model']==model and r['method']==method]
            group_calls=[c for c in calls if c['model']==model and c['method']==method]
            valid=[r for r in selected if r.get('comparable') and r.get('score') is not None]
            groups.append(dict(model=model,method=method,finished=len(selected),expected=5,
                report_execution_completed=sum(r['execution_status']=='completed' for r in selected),
                judgments_fully_processed=sum(r.get('judgment_status')=='complete' for r in selected),
                processing_comparable=len(valid),mean_score_when_scoring_comparable=round(mean(r['score'] for r in valid),2) if valid else None,
                mean_score_denominator=len(valid),mean_processing_pending_points=round(mean(r['processing_pending_points'] for r in selected if r.get('processing_pending_points') is not None),2) if any(r.get('processing_pending_points') is not None for r in selected) else None,
                mean_elapsed_seconds=round(mean(r['elapsed_seconds'] for r in selected),2) if selected else None,
                api_calls=len(group_calls),
                estimated_cost_min=round(sum(c['estimated_cost_min'] or 0 for c in group_calls),4),
                estimated_cost_max=round(sum(c['estimated_cost_max'] or 0 for c in group_calls),4)))
    write_csv(folder/'model-method-summary.csv',groups)
    pairs=[]
    for case in dict.fromkeys(t['case_id'] for t in plan):
        for model in dict.fromkeys(t['model'] for t in plan):
            pair={r['method']:r for r in rows if r['case_id']==case and r['model']==model}
            direct,system=pair.get('direct',{}),pair.get('system',{})
            valid=bool(direct.get('comparable') and system.get('comparable'))
            pairs.append(dict(case_id=case,model=model,direct_score=direct.get('score'),system_score=system.get('score'),
                both_scoring_comparable=valid,system_minus_direct=round(system['score']-direct['score'],2) if valid else None,
                direct_processing_pending_points=direct.get('processing_pending_points'),system_processing_pending_points=system.get('processing_pending_points')))
    write_csv(folder/'paired-results.csv',pairs)
    totals=dict(expected_runs=len(plan),finished_runs=len(rows),execution_completed=sum(r['execution_status']=='completed' for r in rows),
        processing_comparable=sum(bool(r.get('comparable')) for r in rows),api_calls=len(calls),
        calls_with_usage=sum(c['has_usage'] for c in calls),calls_without_usage=sum(not c['has_usage'] for c in calls),
        cost_basis='API catalog price range applied only to returned usage; missing usage is unpriced, not free. Not a billing statement.',
        call_errors=sum(bool(c['error']) for c in calls),
        interrupted_attempt_calls=sum(c['interrupted_attempt'] for c in calls),
        input_tokens=sum(c['input_tokens'] or 0 for c in calls),output_tokens=sum(c['output_tokens'] or 0 for c in calls),
        estimated_cost_min=round(sum(c['estimated_cost_min'] or 0 for c in calls),4),
        estimated_cost_max=round(sum(c['estimated_cost_max'] or 0 for c in calls),4),
        stages=dict(Counter(c['stage'] for c in calls)))
    write_json(folder/'totals.json',totals)
    names={'deepseek-v4.1-flash':'DeepSeek','qwen3.8-flash':'Qwen','kimi-k3':'Kimi','glm-5.2':'GLM'}
    text=['# 40次首轮实测结果','',
          f"当前完成 {totals['finished_runs']}/40 次报告级运行，其中 {totals['execution_completed']} 次生成了评审记录；{totals['processing_comparable']} 次满足当前评分器的可比条件。",'',
          '**报告分数不是模型准确率。** 本轮没有专家标准答案，仅有一次重复，结果用于比较处理完成度、输出差异、时延和费用。','',
          '## 逐报告结果','',
          '| 报告 | 模型 | 直接评审 | 系统评审 | 系统减直接 |','| --- | --- | ---: | ---: | ---: |']
    row_index={(r['case_id'],r['model'],r['method']):r for r in rows}
    def score_label(row):
        if not row:return '未完成'
        if row.get('score') is None:return '未出分'
        return str(row['score'])+(' *' if not row.get('comparable') else '')
    for p in pairs:
        direct=row_index.get((p['case_id'],p['model'],'direct'));system=row_index.get((p['case_id'],p['model'],'system'))
        text.append(f"| {p['case_id']} | {names[p['model']]} | {score_label(direct)} | {score_label(system)} | {p['system_minus_direct'] if p['system_minus_direct'] is not None else '—'} |")
    text+=['','带 * 的分数仍有处理错误或封顶范围未确定，不能用于直接排名；未出分不按0分处理。无星号记录中，模型仍可能判定证据不足或适用性待确认，未决分值和评分覆盖率见 results.csv。','',
           '## 处理与资源开销','',
           '请求和费用统计全部已保存调用，包括中断尝试；完成数、可比分数数目和平均耗时仅统计已结束的组合。评分器可比不代表全部业务判据处理完成。','',
           '| 模型 | 方法 | 完成运行 | 判据处理全完成 | 可比分数数目 | API请求 | 平均秒/报告 | 已知用量费用估算/元 |',
           '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for g in groups:
        text.append(f"| {names[g['model']]} | {'直接' if g['method']=='direct' else '系统'} | {g['finished']}/5 | {g['judgments_fully_processed']} | {g['processing_comparable']} | {g['api_calls']} | {g['mean_elapsed_seconds'] if g['mean_elapsed_seconds'] is not None else '—'} | {g['estimated_cost_min']:.2f}–{g['estimated_cost_max']:.2f} |")
    text+=['',f"全部请求：{totals['api_calls']} 次，其中 {totals['call_errors']} 次接口/响应错误，{totals['calls_without_usage']} 次没有返回用量。已返回用量：输入 {totals['input_tokens']:,} token、输出 {totals['output_tokens']:,} token。",'',
           f"已知用量的目录价格估算：{totals['estimated_cost_min']:.2f}–{totals['estimated_cost_max']:.2f} 元。包括 {totals['interrupted_attempt_calls']} 次中断尝试请求。若存在缺失用量，尚有无法估算的费用，区间不是账单总额上限。",'',
           '## 可复核文件','',
           '- [40次运行明细](results.csv)：含原始合计、最终分、未决分值、耗时与状态。',
           '- [模型与方法汇总](model-method-summary.csv)及[配对结果](paired-results.csv)。',
           '- [逐调用用量](calls.csv)、[处理错误](processing-errors.csv)、[逐项判断](predictions.jsonl)。',
           '- 各运行目录中的 run.json 与 call-*.json 保留原始答案；未为提高分数或完成率而替换输出。',
           '- [执行说明](执行说明.md)解释样本、方法、模型、评分口径和复现命令。','']
    (folder/'结果说明.md').write_text('\n'.join(text),encoding='utf-8')
    print(json.dumps(totals,ensure_ascii=False))


if __name__=='__main__':main()

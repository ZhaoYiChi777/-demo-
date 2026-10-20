"""Export this diagnosed snapshot without changing data or rerunning review.

The narrative is specific to REV-53bd8707f14b; do not reuse it for later runs.
"""
import argparse
import collections
import csv
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from hybrid_retrieval import windows


def export(project_id, connection_result):
    conn=sqlite3.connect((ROOT/'backend/data/review.db').as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    runs=[json.loads(row['payload']) for row in conn.execute('SELECT payload FROM wb_runs WHERE project_id=? ORDER BY rowid DESC',(project_id,))]
    if not runs:raise SystemExit('此项目没有评审快照')
    run=next((r for r in runs if r['id']=='REV-53bd8707f14b'),None)
    if run is None:raise SystemExit('没有找到本次诊断的固定评审快照 REV-53bd8707f14b')
    output=ROOT/'artifacts'/'review-diagnostics'/project_id/run['id']
    output.mkdir(parents=True,exist_ok=True)
    local=lambda value:datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).isoformat() if value else '未记录'
    now=datetime.now(timezone(timedelta(hours=8))).isoformat()
    def write_csv(name,columns,rows):
        with (output/name).open('w',encoding='utf-8-sig',newline='') as file:
            writer=csv.writer(file);writer.writerow(columns);writer.writerows(rows)
    labels={'pass':'符合','fail':'不符合','pending':'待确认'}
    categories={'common':'通用标准','special':'专项标准','topic':'课题要求'}
    checks=run['checks'];sources=run['sources']
    write_csv('全部规则明细.csv',['规则编号','规则名称','类别','适用专业','执行方式','结果','当前原因','关键词','未出现的关键词','字段命中段数','语义命中段数','证据位置','证据摘录','建议'],[
        [c['id'],c['rule']['name'],categories[c['category']],c['rule']['specialty'],'字段存在性检查' if c['rule']['mode']=='presence' else '人工核验',labels[c['result']],c['reason'],
         '；'.join(c['rule']['terms']),'；'.join(c['missing']),c.get('retrieval',{}).get('field_matches',0),c.get('retrieval',{}).get('semantic_matches',0),
         '；'.join(f"{e['filename']} / 段落 {e['id']}" for e in c['evidence']),
         '\n\n'.join(f"段落 {e['id']}：{e['text']}" for e in c['evidence']),c['suggestion']] for c in checks])
    write_csv('材料解析明细.csv',['来源记录','文件名','分类','资料包','解析状态','解析错误原文','解析方式','入库时间（UTC+8）','纳入评审文本'],[
        [s['id'],s['filename'],s['document_category'],s.get('material_package_id'),s['parse_status'],s.get('parse_error',''),s.get('parse_method',''),local(s['created_at']),
         '是' if s['document_id'] in {c['document_id'] for c in run['chunks']} else '否'] for s in sources])
    batches=[]
    for wi,parts in enumerate(windows(run['chunks']),1):
        for ri in range(0,len(checks),32):
            group=checks[ri:ri+32]
            batches.append({'组序号':len(batches)+1,'材料窗口':wi,'规则数':len(group),'规则名称':[c['rule']['name'] for c in group],
                '段落编号':[p['paragraph_id'] for p in parts],'窗口字符数':sum(len(p['text']) for p in parts),
                '结论':'快照显示全部组未完成；本组错误及耗时未保存','证据性质':'按当前分组代码和本次快照还原的请求计划，并非原始调用日志'})
    write_csv('语义检索27组计划还原.csv',['组序号','材料窗口','规则数','窗口字符数','段落编号','规则名称','结论','证据性质'],[
        [b['组序号'],b['材料窗口'],b['规则数'],b['窗口字符数'],','.join(map(str,b['段落编号'])),'；'.join(b['规则名称']),b['结论'],b['证据性质']] for b in batches])
    agent=[dict(row) for row in conn.execute('SELECT * FROM wb_agent_logs WHERE project_id=? ORDER BY started_at',(project_id,))]
    legacy=[dict(row) for row in conn.execute('SELECT a.* FROM review_action_log a JOIN review_sessions s ON a.session_id=s.id WHERE s.project_id=? ORDER BY a.created_at',(project_id,))]
    access=[]
    for filename in ('workbench-api-out.log','workbench-api-error.log','review-api.log'):
        path=ROOT/'backend/data/logs'/filename
        if path.exists():
            access.extend(f'{filename}:{i}: {line}' for i,line in enumerate(path.read_text(encoding='utf-8',errors='replace').splitlines(),1) if project_id in line or run['id'] in line)
    (output/'现存访问日志.txt').write_text('\n'.join(access) or '未找到关联访问日志',encoding='utf-8')
    counts=collections.Counter((c['category'],c['rule']['mode'],c['result']) for c in checks)
    parsed=collections.Counter(s['parse_status'] for s in sources)
    failures=collections.Counter(s.get('parse_error','') for s in sources if s['parse_status']=='parse_failed')
    trace=[
        {'步骤':'材料入库与解析','时间范围':[local(min(s['created_at'] for s in sources)),local(max(s['created_at'] for s in sources))],
         '结果':dict(parsed),'解析失败原因':dict(failures),'证据性质':'来源记录保存的状态；不是本次重审重新解析的日志'},
        {'步骤':'选择评审范围','结果':run['scope_name'],'资料包编号':run.get('material_package_id'),'证据性质':'评审快照'},
        {'步骤':'读取可评审原文','文档数':len({c['document_id'] for c in run['chunks']}),'段落数':len(run['chunks']),'字符数':sum(len(c['text']) for c in run['chunks']),'证据性质':'评审快照'},
        {'步骤':'规则选择','规则数':len(checks),'专业':run['project'].get('specialty'),'证据性质':'评审快照；未设置专业时不过滤专项规则，见 workbench.py rules'},
        {'步骤':'字段匹配','有证据规则数':sum(bool(c['evidence']) for c in checks),'无证据规则数':sum(not c['evidence'] for c in checks),'证据性质':'评审快照'},
        {'步骤':'语义检索','结果':run.get('retrieval'),'分组计划':batches,'每组原始错误':'未保存','每组开始结束及耗时':'未保存','证据性质':'汇总状态已保存，分组计划由快照还原'},
        {'步骤':'逐项判断与汇总','结果':run['counts'],'类别统计':[{'类别':cat,'方式':mode,'结论':result,'数量':n} for (cat,mode,result),n in counts.items()],
         '课题要求':run['brief'],'证据性质':'评审快照；manual 模式始终待人工核验'},
        {'步骤':'保存评审快照','时间':local(run['created_at']),'评审记录':run['id'],'证据性质':'created_at 在检索结束后赋值，不能视为评审开始时间'},
        {'步骤':'当前接口诊断','结果':connection_result,'状态记录时间':now,'证据性质':'本次自查期间独立连通性测试的结果，非27组历史请求的逐组日志'},
    ]
    evidence={'项目编号':project_id,'评审编号':run['id'],'导出时间':now,'流程证据':trace,'已保存智能体日志':agent,'旧版操作日志':legacy,
              '评审历史摘要':[{k:r.get(k) for k in ('id','created_at','counts','retrieval')} for r in runs]}
    (output/'工作流程证据.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    md=f'''# 项目评审诊断与流程记录

项目：{project_id}；评审记录：{run['id']}。快照保存时间：{local(run['created_at'])}。

该界面显示的是已保存评审快照。84 项待确认不等于 84 项匹配失败；此轮也没有形成 0 个缺陷的质量保证。

## 结论构成

| 检查情况 | 数量 | 形成结果的原因 |
| --- | ---: | --- |
| 项目基本信息字段、申报章节完整性 | 2 | 字段存在性规则，要求的文字均出现，自动标为符合 |
| 有字段证据的通用／专项人工检查 | 59 | 检索到原文，但人工模式不会自动给出符合或不符合 |
| 无字段证据的通用／专项人工检查 | 23 | 字面关键词未命中，语义检索也未完成；不等同于材料一定没有依据 |
| 课题检查 | 2 | 任务书／验收要求为空，缺少比较基准；即使已有关键词证据也仍待确认 |

数量为 2 + 59 + 23 + 2 = 86。共 84 条人工核验规则（通用 9、专项 73、课题 2），仅 2 条自动字段规则。

## 语义检索为什么失败

本次快照的模型为 {run.get('retrieval',{}).get('model')}，完成 0 / 27 组。27 组是 9 个材料窗口 × 3 个规则组（每组最多 32 条：32 / 32 / 22），不是 27 条规则，也不是 27 份报告。

本次自查重新通过项目模型网关请求，结果：**{connection_result}**。这确认当前密钥在配置的 DashScope 地址鉴权失败。它与语义检索完全不可用的现象一致，但不能冒充本次 27 组全部返回 401 的历史证据。

`backend/hybrid_retrieval.py` 的异常分支捕获异常后返回空值，未保存 HTTP 状态、错误内容、请求标识或每组耗时。因此无法从已保存记录确认这 27 次各自的原始失败原因。完成数为 0 表示没有一组取得可用的证据列表，并非模型明确判断所有规则都不相关。

## 材料解析问题

共有 {len(sources)} 条来源记录：解析成功 {parsed['parsed']}、失败 {parsed['parse_failed']}、压缩包容器 {parsed['container']}。这里按来源记录计数，存在重复文件引用。

- 申报材料：13 条成功、19 条失败、1 条压缩包容器。
- 模板：2 条成功，未作为申报原文参加本次规则检查。
- 标准资料：5 条失败，未作为申报原文参加本次规则检查。
- 保存的失败原因为缺少 `libreoffice`（11 条）和 `pdftoppm`（13 条）。这些是原入库时的失败记录，不是本次重审重新解析得到的错误。
- 实际评审仅读取 {len({c['document_id'] for c in run['chunks']})} 个去重文档、{len(run['chunks'])} 个段落、{sum(len(c['text']) for c in run['chunks'])} 个字符。未解析文件没有进入检索。

本次环境检查可找到 `pdftoppm`，但未找到 `libreoffice`、`soffice` 或 `tesseract`。旧解析失败状态仍在，需重新解析后才能判断恢复情况；不能把安装状态等同于材料已解析成功。

## 规则与评审范围问题

项目专业为空。现有代码在未设置专业时不过滤专项规则，所以本轮纳入 6 条“全部”规则和 80 条“汽机”规则，尚未逐条判断业务适用性。

字段检索使用完整关键词的字面包含关系。例如“额定压力/流量”不会自动拆成“压力”或“流量”，“强度/应力/振动/压力计算书”也作为完整检索词。这会漏掉同义表达；语义服务失败后无法补充召回。23 条无证据规则及完整关键词见 CSV。

当前选择“整个项目”，会把多个资料包的报告合并检索。材料中还包括“20260821发电企业申报项目智能评审需求说明 - 20260824.docx”，被分类为申报材料。不同项目报告和系统需求说明混在一起，命中的字段不一定来自当前想评审的单份报告。应先核对资料分类和评审范围，再解释单个报告结论。

## 可追溯流程

| 阶段 | 已保存证据 | 结果或限制 |
| --- | --- | --- |
| 原始导入与解析 | 来源记录时间、状态、parse_error | 见材料解析明细 |
| 选择范围 | material_package_id 为空 | 整个项目，合并多个资料包 |
| 原文输入 | 快照保存原文片段 | 262 段，136219 字符 |
| 规则选择 | 快照保存 86 条规则及版本 | 专业未设置，全部启用规则进入 |
| 字段检索 | 每条规则的证据、缺失词 | 63 条有证据，23 条无证据 |
| 语义检索 | 仅汇总状态 | 0 / 27，无每组异常及耗时 |
| 判断 | 每条规则的模式、理由和结果 | 2 符合、0 不符合、84 待确认 |
| 聚合 | 三类结果和总结果 | 存在待确认，综合结论待确认 |
| 保存快照 | {local(run['created_at'])} | 这是保存时间，开始时间及总耗时未记录 |
| 访问日志 | 本机残留 API 日志 | 没有请求时间与评审编号，无法唯一对应本次执行 |
| 会话／操作日志 | 当前项目 {len(agent)} 条智能体日志、{len(legacy)} 条旧版日志 | 当前直接评审入口没有生成全流程操作日志 |

## 文件索引

- [全部86条规则、结果原因与证据](全部规则明细.csv)
- [全部40条来源及解析错误](材料解析明细.csv)
- [27组语义检索计划还原](语义检索27组计划还原.csv)（不是逐组原始日志）
- [工作流程证据与现存操作日志](工作流程证据.json)
- [现存项目访问日志](现存访问日志.txt)

本次自查只读取数据库并导出文件，没有修改项目、材料或评审结论，也没有重新执行整轮评审。完整原始流程日志没有被系统保存，不能补造。下一次需要在各阶段记录开始／结束时间、结果、模型错误及关联评审编号，才能形成真正的全流程日志。
'''
    (output/'诊断报告.md').write_text(md,encoding='utf-8')
    assert len(checks)==86 and len(sources)==40 and len(batches)==27,'此报告模板限定本次诊断快照'
    print(output/'诊断报告.md')
    print(f'Exported {len(checks)} checks, {len(sources)} sources, {len(batches)} reconstructed batches; DB read-only.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('project_id')
    parser.add_argument('--connection-result',default='未在本次导出中测试')
    args=parser.parse_args()
    export(args.project_id,args.connection_result)

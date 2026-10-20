'use strict';
const QUALITY_POLICY='demo-quality-v1';
const QUALITY_DIMENSIONS=[['material','材料完整性',20],['common','通用规范',25],['special','专项技术',35],['topic','课题目标与验收',20]];
const qualityKey=c=>c.category==='common'&&c.rule.mode==='presence'?'material':c.category;
function qualityScore(run){
  if(run.quality_score?.policy)return run.quality_score;
  const dimensions=QUALITY_DIMENSIONS.map(([id,name,weight])=>{
    const checks=run.checks.filter(c=>qualityKey(c)===id&&c.result!=='not_applicable'&&!(c.judgment?.status==='completed'&&c.judgment?.applicability==='not_applicable'&&!c.reviewed_by)),total=checks.length;
    const counts=Object.fromEntries(['pass','fail','pending'].map(v=>[v,checks.filter(c=>c.result===v).length]));
    const score=total?100*(counts.pass+counts.pending*.5)/total:null;
    return {id,name,weight,total,...counts,score,contribution:score===null?null:score*weight/100};
  });
  return {policy:QUALITY_POLICY,dimensions,total:dimensions.some(d=>!d.total)?null:Math.round(dimensions.reduce((s,d)=>s+d.contribution,0)*10)/10,provisional:run.checks.some(c=>c.result==='pending')};
}
const scoreDimensions=run=>qualityScore(run).dimensions;
const comparisonScore=run=>run&&qualityScore(run).comparable!==false?qualityScore(run).total:null;
const qualityRowId=row=>String(row.project?.item_id??row.project?.id??row.pack.id);
function compareQualityRows(a,b,sort){
  if(!a.run||!b.run)return Number(!!b.run)-Number(!!a.run)||qualityRowId(a).localeCompare(qualityRowId(b));
  const primary=sort==='score'?(comparisonScore(b.run)??-1)-(comparisonScore(a.run)??-1):sort==='time'?new Date(b.run.created_at)-new Date(a.run.created_at):a.run.counts[sort]-b.run.counts[sort];
  return primary||a.run.counts.pending-b.run.counts.pending||new Date(b.run.created_at)-new Date(a.run.created_at)||qualityRowId(a).localeCompare(qualityRowId(b));
}
function rankExplanation(run,context){
  if(!context)return '';
  const {rows,sort}=context,index=rows.findIndex(r=>r.run?.id===run.id),previous=rows[index-1]?.run,next=rows[index+1]?.run;
  const names={score:'参考分由高到低',fail:'不符合项由少到多',pending:'待确认项由少到多',time:'评审时间由新到旧'};
  let detail='当前范围内没有排在此报告之前的记录。';
  if(index===0&&sort==='score')detail=comparisonScore(run)===null?'当前缺少维度规则，暂不能按分数区分质量。':next&&comparisonScore(next)!==null?comparisonScore(next)===comparisonScore(run)?'本报告并列最高分，按待确认项数、评审时间、记录编号依次确定展示顺序。':`本报告在当前范围内得分最高，比下一名高 ${(comparisonScore(run)-comparisonScore(next)).toFixed(1)} 分。`:'当前范围只有这一份可计算分数的报告，尚无其他评分可比较。';
  if(previous){
    if(sort==='score')detail=comparisonScore(run)===null?'当前核验未完成或评分条件不足，暂不能生成可比较的总分。':comparisonScore(previous)===comparisonScore(run)?'与上一名同分，按待确认项数、评审时间、记录编号依次决定顺序。':`上一名 ${comparisonScore(previous)?.toFixed(1)} 分，本报告低 ${((comparisonScore(previous)??0)-comparisonScore(run)).toFixed(1)} 分。`;
    else if(sort==='time')detail=`上一名评审时间：${date(previous.created_at)}；本报告：${date(run.created_at)}。`;
    else detail=`上一名${sort==='fail'?'不符合':'待确认'} ${previous.counts[sort]} 项，本报告 ${run.counts[sort]} 项。相同时按待确认项数、评审时间、记录编号依次决定顺序。`;
  }
  if(new Set(rows.filter(r=>r.run).map(r=>qualityScore(r.run).policy)).size>1)detail='列表包含不同评分口径，当前顺序仅用于浏览；请统一重新评审后再比较分数高低。';
  return `<section class="rank-explanation"><h3>为什么排在这里</h3><p>当前筛选范围：第 <b>${index+1}</b> / ${rows.length} 位，按“${names[sort]}”排列。</p><p>${esc(detail)}</p><small>仅比较当前列表中的最近评审。规则版本、适用专业或课题要求不同的项目，排名仅供筛选参考。未审查记录排在最后；按参考分排序时，缺少维度的记录排在有分数记录之后。</small></section>`;
}
function scorePanel(run,context){
  if(!run)return '<div class="panel-body muted">尚无评分，完成审查后显示。</div>';
  const quality=qualityScore(run);
  if(['fsr-70-30-v1','fsr-70-30-v2','fsr-70-30-v3','fsr-70-30-v4','fsr-70-30-v5'].includes(quality.policy))return fsrScorePanel(run,quality,context);
  return `<div class="panel-body"><div class="score-total">${quality.total?.toFixed(1)??'—'}<small> / 100 ${quality.provisional?'暂评参考分':'参考分'}</small></div><p class="muted">演示评分口径 · 第 1 版${run.quality_score?'':' · 历史报告按当前口径计算'}</p>${rankExplanation(run,context)}<h3>为什么得这个分</h3>${quality.dimensions.map(d=>{
    const checks=run.checks.filter(c=>qualityKey(c)===d.id),issues=checks.filter(c=>c.result!=='pass');
    return `<details class="score-dimension"><summary><strong>${d.name} · 权重 ${d.weight}%</strong><span>${d.score?.toFixed(1)??'缺少规则'}${d.score===null?'':' 分'}</span></summary><progress max="100" value="${d.score??0}" aria-label="${d.name}"></progress><small>符合 ${d.pass} / ${d.total} 项 · 不符合 ${d.fail} 项 · 待确认 ${d.pending} 项<br>计入总分 ${d.contribution?.toFixed(2)??'—'} / ${d.weight} 分</small><p>${d.total?`每项占本维度 ${(100/d.total).toFixed(2)} 分；不符合扣全额，待确认暂扣一半。`:'本维度未配置检查项，总分不予计算。'}</p>${issues.map(c=>`<article class="score-deduction"><b>${esc(c.rule.name)} · ${RESULT[c.result]}</b><p>本维度${c.result==='pending'?'暂扣':'扣'} ${((c.result==='pending'?50:100)/d.total).toFixed(2)} 分，影响总分 ${((c.result==='pending'?.5:1)*d.weight/d.total).toFixed(2)} 分。</p><p>${esc(c.reason)}</p>${button('查看检查项与证据',`data-quality-check="${esc(c.id)}" data-quality-run="${esc(run.id)}"`,'subtle')}</article>`).join('')||(d.total?'<p>本维度全部符合，无扣分。</p>':'')}</details>`;
  }).join('')}<details class="quality-policy"><summary>查看权重与扣分规则</summary><p>材料完整性 20%、通用规范 25%、专项技术 35%、课题目标与验收 20%。维度内检查项等权；符合不扣分、不符合扣除该项全部分值、待确认暂扣该项 50% 分值。总分为各维度得分乘权重后求和，保留一位小数。</p><p>材料完整性只包含通用规则中的字段存在性检查，其余通用规则计入通用规范；专项和课题规则各计入对应维度。同一检查项不重复计分，缺少任一维度时不计算总分。</p><p>待确认项完成复核后重算分数。此口径为演示配置，尚非企业正式评分标准；得分不替代符合性结论。</p></details></div>`;
}
document.addEventListener('click',event=>{const b=event.target.closest('[data-quality-check]');if(!b)return;guarded(async()=>{
  const run=await api(`/api/workbench/runs/${id(b.dataset.qualityRun)}`);
  await selectProject(run.project_id);state.run=run;state.scope=run.material_package_id;state.step='decision';state.selected=b.dataset.qualityCheck;state.category='all';state.result='all';state.mode='list';navigate('review');
});});

function fsrScorePanel(run,q,context){
  const policyNote=q.policy==='fsr-70-30-v5'?'':'<p class="muted">此记录保留旧版评分口径，重新评审后使用第5版规则：有效子项独立计分，最高92分。</p>';
  const capPolicy=['fsr-70-30-v4','fsr-70-30-v5'].includes(q.policy)?'最终得分和可得分区间上限均不超过92分；仅专项篇章仍最高49分。关键数据矛盾按分项计分并提示核验。':q.policy==='fsr-70-30-v3'?'仅专项篇章最高49分；关键数据矛盾按分项计分并提示核验，不再默认封顶69分。':'仅专项篇章最高49分；推荐方案、总投资或项目范围存在未解释的根本矛盾最高69分。';
  const pendingBreakdown=q.processing_pending_points===undefined?'':`<p>模型处理未完成：<b>${q.processing_pending_points.toFixed(2)}分</b>；材料或适用性待确认：<b>${q.evidence_pending_points.toFixed(2)}分</b>。${q.comparable===false?'本次暂计分不参与有效评分比较。':''}</p>`;
  const consistencyNotes=(q.decision_notes||[]).map(n=>`<details class="quality-policy"><summary>关键数据仍需核实</summary><p>${esc(n.explanation)}</p><p>${esc(n.reason)}</p>${button('查看问题与证据',`data-quality-check="${esc(n.check_id)}" data-quality-run="${esc(run.id)}"`,'subtle')}</details>`).join('');
  const branches={safety:'安全可靠性',energy:'节能经济性',heat:'供热与灵活性',turbine:'主机本体重大改造',innovation:'科技创新专项'};
  const knownType=Object.hasOwn(branches,q.branch.id),typeSource=q.branch.source==='user'?'手动指定':q.branch.source==='title'?'自动识别':'当前记录';
  const typeControl=`<p>评分类型：${knownType?esc(branches[q.branch.id])+'（'+typeSource+'）':'待确认'}<br><small>${esc(q.branch.reason)}</small></p><details class="quality-policy score-type-adjustment" ${knownType?'':'open'}><summary>${knownType?'识别有误？调整类型':'未能自动识别，请选择评分类型'}</summary><p>评分类型决定专项30分的检查重点，共性70分保持一致。请按报告的主要改造目标选择。</p><label class="form-field">评分类型<select data-score-branch>${options(knownType?branches:{'':'请选择评分类型',...branches},q.branch.id||'')}</select></label><p class="muted">确认后重新评审整份报告并生成新记录，本次选择仅用于此次评审。</p>${button('按此类型重新评审',`data-score-rerun="${esc(run.id)}" ${state.busy?'disabled':''}`)}</details>`;
  return `<div class="panel-body"><div class="score-total">${q.total?.toFixed(1)??'—'}<small> / 100 ${q.provisional?'暂计分项得分':'已确认得分'}${q.grade?' · '+q.grade+'类':''}</small></div><p>${esc(q.label)}</p>${policyNote}${q.scoring_status==='processing_failed'?'<p class="notice">模型处理未完成，暂不生成质量分数，不参与有效评分比较；请恢复模型服务后重试。</p>':''}${pendingBreakdown}${typeControl}<p>判据核验完成度：${q.coverage??'—'}%；当前可得分区间：${q.total===null?'尚未形成':(q.lower_bound??q.total)+'—'+q.upper_bound}。</p>${q.provisional?'<p class="notice">当前按已核验判据计分；若完整可研范围待确认，总分仍为暂计，区间下限考虑49分条件上限。补充证据并复核后更新评分与等级。待确认分值尚未计入；区间上限假设未决判据全部通过，不代表已获得该分数。</p>':''}${q.caps.map(c=>`<p class="notice">已触发 ${c.limit} 分封顶：${esc(c.reason)}</p>`).join('')}${consistencyNotes}${rankExplanation(run,context)}<h3>为什么得这个分</h3>${q.dimensions.map(d=>`<details class="score-dimension"><summary><strong>${esc(d.name)} · ${d.group==='common'?'共性':'类型分支'}</strong><span>${d.excluded?'不适用':d.contribution.toFixed(2)+' / '+d.effective_weight.toFixed(2)+'分'}</span></summary><p>原定 ${d.weight} 分。${d.excluded?'有明确原文证明不适用，已退出计分，权重在同组内归一。':`符合 ${d.pass}、不符合 ${d.fail}、待确认 ${d.pending} 项；未决 ${d.unresolved.toFixed(2)} 分。每个固定判据等分，符合得分，不符合不得分，待确认不预支分值。`}</p>${d.items.map(i=>`<article class="score-deduction"><b>${esc(i.name)} · ${d.excluded?'不适用':i.pending_reason==='processing_error'||i.pending_reason==='retrieval_incomplete'?'模型处理待完成':RESULT[i.result]}</b><p>${esc(i.reason)}</p>${button('查看判据与证据',`data-quality-check="${esc(i.check_id)}" data-quality-run="${esc(run.id)}"`,'subtle')}</article>`).join('')}</details>`).join('')}<details class="quality-policy"><summary>评分与扣分规则</summary><p>${esc(q.explanation)}</p><p>共性：12＋8＋15＋8＋10＋7＋7＋3＝70分；类型分支：8＋8＋7＋7＝30分。${capPolicy}A≥85，B≥75，C≥60，D&lt;60；等级不替代符合性结论。</p></details></div>`;
}
document.addEventListener('click',event=>{
  const b=event.target.closest('[data-score-rerun]');if(!b)return;guarded(async()=>{
    if(state.busy)return;
    const branch=b.parentElement.querySelector('[data-score-branch]').value;
    if(!branch)return toast('请先选择评分类型。');
    const run=await api(`/api/workbench/runs/${id(b.dataset.scoreRerun)}`);
    state.busy=true;b.disabled=true;
    try{
      const fresh=await api(`/api/workbench/projects/${id(run.project_id)}/runs`,'POST',{material_package_id:run.material_package_id,scoring_branch:branch});
      await selectProject(run.project_id);state.run=fresh;state.scope=fresh.material_package_id;state.step='decision';navigate('review');
    }finally{state.busy=false;render();}
  });
});

'use strict';
const comparison = {projectId:null, rows:[], selected:null, sort:'score', loading:false, error:'', request:0};
function reviewEntrances(){return `<section class="review-entries"><article class="panel panel-body"><h2>单份报告评审</h2><p>选择一份报告，建立一个项目并开始审查。</p>${button('新建单个项目','data-single-report','primary')}</article><article class="panel panel-body"><h2>一个项目 · 多份报告</h2><p>一次建立一个项目组，上传多份报告；逐份审查、评分，并在组内排序对比。</p>${button('新建项目组','data-batch-new','primary')} ${button('查看项目组与报告','data-batch-overview')}</article></section><div class="row wrap" style="margin-bottom:20px"><span class="muted">已有项目需要管理报告与附件？</span>${button('查看项目内多报告对比','data-comparison','subtle')}</div>`;}
function singleReportForm(){modal('单份报告审查',`<p class="muted">系统为本次报告建立独立项目记录。专业判断仍需人工复核。</p><label class="form-field">报告文件<input type="file" name="report" accept=".docx,.doc,.pdf,.txt" required></label>${field('报告名称（不填则使用文件名）','name','','input')}${field('课题目标与验收要求','brief','','textarea')}${briefUpload('brief')}`,button('取消','type="button" data-close')+'<button class="primary" type="submit">上传并开始审查</button>','single-report-form');}
async function submitSingleReport(form){
  const values=new FormData(form),file=values.get('report');
  if(!file?.size)throw new Error('请选择一份非空报告。');
  const data=new FormData();data.append('name',String(values.get('name')).trim()||file.name.replace(/\.[^.]+$/,''));data.append('project_type','技术改造');
  const project=await api('/api/projects','POST',data), projectId=project.project_id||project.id;
  toast('正在上传并解析报告…');
  try{
    const upload=new FormData();upload.append('file',file);upload.append('document_type','project_report');await api(`/api/projects/${id(projectId)}/documents`,'POST',upload);
    const material=await api(`/api/workbench/projects/${id(projectId)}/materials`);
    for(const source of material.sources)await api(`/api/projects/${id(projectId)}/sources/${source.id}/category`,'PATCH',{category:'project_report'});
    await api(`/api/workbench/projects/${id(projectId)}/brief`,'PUT',{text:String(values.get('brief')||'')});
    $('#editor').close();await refreshProjects();await selectProject(projectId);await runReview();
  }catch(error){await refreshProjects();$('#editor').close();await selectProject(projectId);throw new Error(`${error.message} 已保留项目，可在材料页继续处理。`);}
}
async function loadComparison(projectId){
  const request=++comparison.request;comparison.projectId=projectId;comparison.loading=true;comparison.error='';comparison.rows=[];comparison.selected=null;render();
  try{
    if(!projectId)return;
    const [history,packs]=await Promise.all([api(`/api/workbench/projects/${id(projectId)}/runs`),api(`/api/projects/${id(projectId)}/material-packages`)]);
    const runs=await Promise.all(history.runs.map(r=>api(`/api/workbench/runs/${id(r.id)}`)));
    if(request!==comparison.request)return;
    const latest=new Map();
    for(const run of runs){if(run.material_package_id&&!latest.has(run.material_package_id))latest.set(run.material_package_id,run);}
    comparison.rows=packs.material_packages.map(pack=>({pack,run:latest.get(pack.id)||null}));
    comparison.selected=comparison.rows.find(row=>row.run)?.pack.id||null;
  }catch(error){if(request===comparison.request)comparison.error=error.message;}
  finally{if(request===comparison.request){comparison.loading=false;if(state.view==='comparison')render();}}
}
function renderComparison(){
  const controls=`<div class="panel panel-body row wrap"><label>选择项目 <select id="comparison-project">${options({'':'请选择项目',...Object.fromEntries(state.projects.map(p=>[p.id,projectName(p)]))},comparison.projectId||'')}</select></label><label>排序方式 <select id="comparison-sort">${options({score:'参考分由高到低',pending:'待确认项由少到多',fail:'不符合项由少到多',time:'评审时间由新到旧'},comparison.sort)}</select></label>${button('刷新结果','data-refresh-comparison')}${button('管理报告与资料包','data-manage-comparison')}</div>`;
  const title=heading('多报告审查','每个资料包对应一份待审报告及其附件；展示每个资料包最近一次评审结果。',button('返回导航','data-view="overview"'));
  if(comparison.loading)return title+controls+'<div class="panel empty">正在加载评审结果…</div>';
  if(comparison.error)return title+controls+`<div class="panel empty">${esc(comparison.error)}</div>`;
  const rows=[...comparison.rows].sort((a,b)=>compareQualityRows(a,b,comparison.sort));
  const selected=rows.find(row=>row.pack.id===comparison.selected);
  const table=`<div class="table-scroll"><table><thead><tr><th>序号</th><th>报告 / 资料包</th><th>结论</th><th>参考分</th><th>不符合 / 待确认</th><th>评审时间</th><th>操作</th></tr></thead><tbody>${rows.map((row,i)=>`<tr class="${row.pack.id===comparison.selected?'comparison-selected':''}"><td>${i+1}</td><td>${button(esc(row.pack.name),`data-score-package="${row.pack.id}"`,'subtle')}<small>${row.pack.document_count||0} 份材料</small></td><td>${row.run?badge(conclusionStyle(row.run),runTitle(row.run)):'未审查'}</td><td>${comparisonScore(row.run)?.toFixed(1)??'—'}</td><td>${row.run?`${row.run.counts.fail} / ${row.run.counts.pending}`:'—'}</td><td>${esc(date(row.run?.created_at))}</td><td>${button(row.run?'重新审查':'开始审查',`data-compare-run="${row.pack.id}"`)}${row.run?button('查看结果',`data-compare-open="${esc(row.run.id)}"`):''}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">请选择项目，并在“管理报告与资料包”中上传报告、设置各自的资料包。</td></tr>'}</tbody></table></div>`;
  return title+controls+`<div class="comparison-layout">${panel('审查结果排序表','点击报告名称，在右侧查看评分。未审查记录排在最后。',table)}${panel('多维度评分',selected?esc(selected.pack.name):'选择一份已审查的报告',scorePanel(selected?.run,{rows,sort:comparison.sort}))}</div>`;
}

document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;guarded(async()=>{
  if(b.hasAttribute('data-single-report'))return singleReportForm();
  if(b.hasAttribute('data-comparison')){navigate('comparison');return loadComparison(state.project?.id||state.projects[0]?.id||'');}
  if(b.hasAttribute('data-refresh-comparison'))return loadComparison(comparison.projectId);
  if(b.hasAttribute('data-manage-comparison')){if(!comparison.projectId)return navigate('projects');await selectProject(comparison.projectId,'projects');return;}
  if(b.dataset.scorePackage){comparison.selected=Number(b.dataset.scorePackage);render();return;}
  if(b.dataset.compareRun){await selectProject(comparison.projectId);return runReview(Number(b.dataset.compareRun));}
  if(b.dataset.compareOpen){await selectProject(comparison.projectId);const run=await api(`/api/workbench/runs/${id(b.dataset.compareOpen)}`);state.run=run;state.scope=run.material_package_id;state.step='decision';render();}
});});
document.addEventListener('change',event=>{if(event.target.id==='comparison-project')guarded(()=>loadComparison(event.target.value));if(event.target.id==='comparison-sort'){comparison.sort=event.target.value;render();}});

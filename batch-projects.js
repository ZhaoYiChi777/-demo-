'use strict';
const projectBatch={batchId:'',batches:[],rows:[],selected:null,sort:'score',filter:'all',search:'',loading:false,error:'',busy:false,progress:'',request:0,files:new Map()};
let batchDraft=[];
const batchRowKey=p=>p.item_id||p.id;
const batchReportName=p=>p.report_name||projectName(p);
const BATCH_STATUS={waiting:'等待上传',uploading:'正在上传',ready:'等待评审',reviewing:'正在评审',completed:'评审完成',error:'处理失败'};
function newProjectMode(){modal('新建评审项目 · 选择模式',`<div class="review-entries"><article class="panel panel-body"><h2>单份报告</h2><p>上传一份报告，创建一个项目并开始审查。</p>${button('创建单个项目','data-single-report','primary')}</article><article class="panel panel-body"><h2>一个项目 · 多份报告</h2><p>一次建立一个项目组，多份报告归入同组，逐份审查并在组内排序。</p>${button('创建项目组','data-batch-new','primary')}</article></div>`);}
function batchProjectForm(){
  if(projectBatch.busy)return toast('当前批次仍在处理，可完成后再创建下一批。');
  batchDraft=[];
  modal('新建项目组 · 上传多份报告',`<p class="muted">本次只建立一个项目，以项目组管理。支持一次选择 1–50 份 Word、PDF 或 TXT 报告；每份报告独立审查、评分，结果在组内排序。</p>${field('项目名称（项目组名称）','batch_name','','input','required maxlength="160"')}<label class="form-field">项目类型<select name="project_type">${options({'技术改造':'技术改造','新建系统':'新建系统','安全可靠':'安全可靠'},'技术改造')}</select></label><label class="form-field">选择多份报告<input id="batch-files" type="file" accept=".docx,.doc,.pdf,.txt" multiple required></label><div id="batch-file-mapping"></div>${field('项目组课题要求（适用于组内全部报告，可选）','common_brief','','textarea')}${briefUpload('common_brief')}`,button('取消','type="button" data-close')+'<button type="submit" class="primary">创建项目组并逐份评审</button>','batch-project-form');
}
function renderBatchMapping(){
  const root=document.getElementById('batch-file-mapping');if(!root)return;
  root.innerHTML=`<p>已选择 ${batchDraft.length} 份报告，将建立 1 个项目组，组内包含 ${batchDraft.length} 份独立审查报告。</p>${batchDraft.map((file,i)=>`<fieldset class="batch-file-row"><legend>报告 ${i+1}：${esc(file.name)}</legend><label class="form-field">报告名称<input name="report_name_${i}" value="${esc(file.name.replace(/\.[^.]+$/,''))}" required maxlength="160"></label></fieldset>`).join('')}`;
}
async function submitBatchProjects(form){
  if(projectBatch.busy)return;
  if(!batchDraft.length||batchDraft.length>50)throw new Error('请选择 1–50 份报告。');
  const values=new FormData(form),files=[...batchDraft];
  const payload={name:String(values.get('batch_name')).trim(),project_type:values.get('project_type'),brief:String(values.get('common_brief')||''),items:files.map((file,i)=>({name:String(values.get(`report_name_${i}`)||'').trim(),filename:file.name}))};
  const batch=await api('/api/workbench/batches','POST',payload);
  batch.items.forEach((item,i)=>projectBatch.files.set(item.item_id,files[i]));
  projectBatch.batchId=batch.id;projectBatch.filter='all';projectBatch.search='';$('#editor').close();
  await refreshProjects();await navigate(USER_MODE?`batch/group/${id(batch.id)}`:'batch');
  await processBatchRows(projectBatch.rows.filter(row=>row.project.batch_id===batch.id));
}
async function loadProjectBatch(){
  if(projectBatch.busy)return;
  const request=++projectBatch.request;projectBatch.loading=true;projectBatch.error='';if(state.view==='batch')render();
  try{
    const batches=await api('/api/workbench/batches');
    if(!USER_MODE&&!projectBatch.initialized&&!projectBatch.batchId)projectBatch.batchId=batches.batches.find(b=>b.project_id)?.id||'';
    projectBatch.initialized=true;
    const data=await api(`/api/workbench/project-comparison${projectBatch.batchId?'?batch_id='+id(projectBatch.batchId):''}`);
    if(request!==projectBatch.request)return;
    projectBatch.batches=batches.batches;projectBatch.rows=data.rows;
    if(!data.rows.some(row=>batchRowKey(row.project)===projectBatch.selected))projectBatch.selected=batchRowKey(data.rows.find(row=>row.run)?.project||data.rows[0]?.project||{});
  }catch(error){if(request===projectBatch.request)projectBatch.error=error.message;}
  finally{if(request===projectBatch.request){projectBatch.loading=false;if(state.view==='batch')render();}}
}
function filteredBatchRows(){
  const search=projectBatch.search.trim().toLowerCase();
  return projectBatch.rows.filter(row=>{
    const p=row.project;
    const match=projectBatch.filter==='all'||(projectBatch.filter==='error'?p.batch_status==='error':projectBatch.filter==='unreviewed'?!row.run:row.run?.result===projectBatch.filter);
    return match&&(!search||`${batchReportName(p)} ${projectName(p)} ${p.filename||''}`.toLowerCase().includes(search));
  }).sort((a,b)=>compareQualityRows(a,b,projectBatch.sort));
}
function renderProjectBatch(){
  const rows=filteredBatchRows(),selected=rows.find(row=>batchRowKey(row.project)===projectBatch.selected);
  const group=projectBatch.batches.find(b=>b.id===projectBatch.batchId),grouped=!!group?.project_id;
  const unit=grouped?'份报告':'个项目';
  const actions=button('＋ 新建项目组',`data-batch-new ${projectBatch.busy?'disabled':''}`,'primary')+button('返回导航','data-view="overview"');
  const title=heading('项目组与报告对比',grouped?`当前项目组：${group.name}。每行是一份报告，独立使用本报告证据形成评审结果，并在组内排序。`:'选择项目组查看组内报告排序。历史批次保留原有的跨项目对比方式。',actions);
  const controls=`<div class="panel panel-body batch-controls"><label>项目组 / 历史批次<select id="project-batch-scope" ${projectBatch.busy?'disabled':''}>${options({'':'全部项目（整体评审）',...Object.fromEntries(projectBatch.batches.map(b=>[b.id,`${b.name}（${b.total} ${b.project_id?'份报告':'个项目 · 历史批次'}）`]))},projectBatch.batchId)}</select></label><label>逐项符合性筛选<select id="project-batch-filter">${options({all:'全部结果',pass:'符合',fail:'不符合',pending:'待确认',unreviewed:'尚未评审',error:'处理失败'},projectBatch.filter)}</select></label><label>排序方式<select id="project-batch-sort">${options({score:'参考分由高到低',fail:'不符合项由少到多',pending:'待确认项由少到多',time:'评审时间由新到旧'},projectBatch.sort)}</select></label><label>搜索报告<input id="project-batch-search" type="search" value="${esc(projectBatch.search)}" placeholder="项目或报告名称"></label>${button('刷新结果','data-batch-refresh')}${projectBatch.batchId?button('继续未完成报告',`data-batch-resume ${projectBatch.busy?'disabled':''}`):''}</div>`;
  const progress=projectBatch.progress?`<div class="notice batch-progress" role="status">${esc(projectBatch.progress)}</div>`:'';
  if(projectBatch.loading)return title+controls+progress+'<div class="panel empty">正在加载项目结果…</div>';
  if(projectBatch.error)return title+controls+`<div class="panel empty">${esc(projectBatch.error)}</div>`;
  const counts=`${grouped?'1 个项目组 · ':''}共 ${projectBatch.rows.length} ${unit} · ${projectBatch.rows.filter(r=>r.run).length} 份已有评审 · ${projectBatch.rows.filter(r=>r.project.batch_status==='error').length} 份处理失败 · 当前显示 ${rows.length} 项`;
  const table=`<div class="table-scroll"><table class="batch-results"><thead><tr><th>序号</th><th>项目 / 报告</th><th>处理进度</th><th>评审结论</th><th>参考分</th><th>不符合 / 待确认</th><th>评审时间</th><th>操作</th></tr></thead><tbody>${rows.map((row,i)=>{
    const p=row.project,key=batchRowKey(p),disabled=projectBatch.busy?'disabled':'';
    return `<tr data-batch-row="${esc(key)}" class="${key===projectBatch.selected?'comparison-selected':''}"><td>${i+1}</td><td>${button(esc(batchReportName(p)),`data-batch-score="${esc(key)}"`,'subtle')}<small>${esc(p.filename||'项目材料')}</small></td><td>${esc(BATCH_STATUS[p.batch_status]||(row.run?'评审完成':'尚未评审'))}${p.error?`<small class="fail-text">${esc(p.error)}</small>`:''}</td><td>${row.run?badge(conclusionStyle(row.run),runTitle(row.run)):'—'}</td><td>${comparisonScore(row.run)?.toFixed(1)??'—'}</td><td>${row.run?`${row.run.counts.fail} / ${row.run.counts.pending}`:'—'}</td><td>${esc(date(row.run?.created_at))}</td><td><div class="batch-row-actions">${row.run?button('查看结果',`data-batch-open="${esc(key)}"`):button('查看项目',`data-project="${esc(p.id)}"`)}${p.batch_id?button(p.batch_status==='error'?'重试本报告':row.run?'重新评审':'继续处理',`data-batch-retry="${esc(key)}" ${disabled}`):''}${p.batch_id&&!p.uploaded?`<label class="batch-replace">${projectBatch.files.has(key)?'已选择文件，可继续处理':'选择／更换报告'}<input type="file" accept=".docx,.doc,.pdf,.txt" data-batch-file="${esc(key)}" ${disabled}></label>`:''}</div></td></tr>`;
  }).join('')||'<tr><td colspan="8" class="empty">没有匹配的记录。可调整筛选条件或新建项目组。</td></tr>'}</tbody></table></div>`;
  return title+controls+progress+`<div class="comparison-layout">${panel(grouped?'组内报告排序表':'项目结果排序表',counts,table)}${panel('多维度评分',selected?esc(batchReportName(selected.project)):'点击报告名称查看评分',scorePanel(selected?.run,{rows,sort:projectBatch.sort}))}</div>`;
}
async function processBatchRows(rows){
  if(projectBatch.busy)return;
  if(!rows.length)return toast('本批次没有需要继续处理的项目。');
  projectBatch.busy=true;let completed=0,failed=0;
  try{
    for(const [index,row] of rows.entries()){
      const p=row.project,key=batchRowKey(p);projectBatch.progress=`正在处理 ${index+1} / ${rows.length}：${batchReportName(p)}`;
      try{
        if(!p.uploaded){
          const file=projectBatch.files.get(key);if(!file)throw new Error('请在本报告行重新选择文件，再点击重试。');
          p.batch_status='uploading';if(state.view==='batch')render();
          const body=new FormData();body.append('file',file);
          const uploaded=await api(`/api/workbench/batches/${id(p.batch_id)}/items/${id(key)}/upload`,'POST',body);p.uploaded=1;p.filename=uploaded.filename;
        }
        p.batch_status='reviewing';if(state.view==='batch')render();
        row.run=await api(`/api/workbench/batches/${id(p.batch_id)}/items/${id(key)}/run`,'POST');p.batch_status='completed';p.error='';completed++;
        projectBatch.files.delete(key);
      }catch(error){p.batch_status='error';p.error=error.message;failed++;}
      if(state.view==='batch')render();
    }
    projectBatch.progress=`本次处理结束：${completed} 份评审完成，${failed} 份未完成。${failed?'可在对应报告行补选文件或单独重试。':(USER_MODE?'点击“查看报告”查看结果，或点击“处理问题”继续复核。':'点击报告名称查看评分，或按结果筛选排序。')}`;
    await refreshProjects();
  }finally{projectBatch.busy=false;if(state.view==='batch')render();}
}
document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;guarded(async()=>{
  if(b.hasAttribute('data-batch-new'))return batchProjectForm();
  if(b.hasAttribute('data-batch-overview'))return navigate('batch');
  if(b.hasAttribute('data-batch-refresh'))return loadProjectBatch();
  if(b.hasAttribute('data-batch-resume'))return processBatchRows(projectBatch.rows.filter(r=>r.project.batch_id===projectBatch.batchId&&r.project.batch_status!=='completed'));
  if(b.dataset.batchScore){projectBatch.selected=b.dataset.batchScore;render();}
  if(b.dataset.batchRetry){const row=projectBatch.rows.find(r=>batchRowKey(r.project)===b.dataset.batchRetry);if(row)return processBatchRows([row]);}
  if(b.dataset.batchOpen){const row=projectBatch.rows.find(r=>batchRowKey(r.project)===b.dataset.batchOpen);await selectProject(row.project.id);state.run=await api(`/api/workbench/runs/${id(row.run.id)}`);state.scope=state.run.material_package_id;state.rules=(await api(`/api/workbench/rules?project_id=${id(row.project.id)}${state.scope!==null?'&material_package_id='+id(state.scope):''}`)).rules;state.step='decision';render();}
});});
document.addEventListener('change',event=>{
  const input=event.target;
  if(input.id==='batch-files'){batchDraft=Array.from(input.files||[]);renderBatchMapping();}
  if(input.id==='project-batch-scope'){projectBatch.batchId=input.value;guarded(loadProjectBatch);}
  if(input.id==='project-batch-filter'){projectBatch.filter=input.value;render();}
  if(input.id==='project-batch-sort'){projectBatch.sort=input.value;render();}
  if(input.dataset.batchFile&&input.files[0]){projectBatch.files.set(input.dataset.batchFile,input.files[0]);render();}
});
document.addEventListener('input',event=>{if(event.target.id==='project-batch-search'){projectBatch.search=event.target.value;const pos=event.target.selectionStart;render();const input=document.getElementById('project-batch-search');input.focus();input.setSelectionRange(pos,pos);}});

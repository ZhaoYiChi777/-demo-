'use strict';

const USER_PAGE_SIZE=8;
const TASK_LABELS={active:'待办',unreviewed:'待开始',issues:'待处理',ready:'待归档',archived:'已归档',error:'处理失败',all:'全部任务'};
const REPORT_LABELS={archived:'已归档',pass:'已通过 / 人工确认',issues:'有待处理问题',all:'全部报告'};
const userTasks={rows:[],loading:false,loaded:false,error:'',request:0};
const userUI={taskCategory:null,reportCategory:null,projectMode:null,groupId:null,projectId:null,chatOpen:false,chatLoading:false,chatRestored:false,
  pages:{tasks:1,reports:1,groups:1,singles:1,groupReports:1,projectHistory:1,chat:1},
  filters:Object.fromEntries(['tasks','reports','groups','singles','groupReports','projectHistory','chat'].map(key=>[key,{query:'',after:'',before:''}]))};
const userProjectHistory={projectId:null,runs:[],loading:false,error:'',request:0};
const userRanking={runId:null,rows:[],group:null,error:''};

async function loadUserRanking(run){
  userRanking.runId=run.id;userRanking.rows=[];userRanking.group=null;userRanking.error='';
  try{
    const data=await api('/api/workbench/batches');
    const group=data.batches.find(item=>item.project_id===run.project_id);
    if(!group)return;
    const result=await api(`/api/workbench/project-comparison?batch_id=${id(group.id)}`);
    if(userRanking.runId===run.id){userRanking.rows=result.rows;userRanking.group=group;}
  }catch(error){if(userRanking.runId===run.id)userRanking.error='组内排名暂未加载';}
}
function userScoreValue(run){
  if(!run)return '<span class="user-score-value">—<small>未评审</small></span>';
  const quality=qualityScore(run),value=quality.total;
  const label=value===null?'评分待完成':comparisonScore(run)===null?'暂计 · 不参与排名':quality.provisional?'暂计分':'参考分';
  return `<span class="user-score-value"><strong>${value?.toFixed(1)??'—'}</strong>${quality.grade?`<span class="user-score-grade">${esc(quality.grade)}类</span>`:''}<small>${label}</small></span>`;
}
function userScoreAnalysis(run,context){
  if(!run)return '';
  const source=context?.rows||(userRanking.group?.project_id===run.project_id?userRanking.rows:[]);
  const rows=source.map(row=>row.run?.id===run.id?{...row,run}:row).filter(row=>comparisonScore(row.run)!==null).sort((a,b)=>compareQualityRows(a,b,'score'));
  const index=rows.findIndex(row=>row.run.id===run.id),rank=index>=0?{rows,sort:'score'}:null;
  const status=rank?`组内第 ${index+1} / ${rows.length} 名`:comparisonScore(run)===null?'待核验 · 暂不排名':userRanking.runId===run.id&&userRanking.error?userRanking.error:userRanking.group?.project_id===run.project_id?'历史评审 · 不参与当前排名':'未参与组内排名';
  return `<details class="panel user-score-analysis no-print"><summary><span><strong>分数分析</strong><span class="user-score-rank">${esc(status)}</span></span>${userScoreValue(run)}</summary>${scorePanel(run,rank)}</details>`;
}

function applyUserRoute(value='overview'){
  const parts=String(value||'overview').split('/'),view=NAV[parts[0]]?parts[0]:'overview';
  let path=view;
  if(view==='overview'){
    const category=Object.hasOwn(TASK_LABELS,parts[1])?parts[1]:null;
    if(category!==userUI.taskCategory)userUI.pages.tasks=1;
    userUI.taskCategory=category;if(category)path+=`/${category}`;
  }
  if(view==='reports'){
    const category=Object.hasOwn(REPORT_LABELS,parts[1])?parts[1]:null;
    if(category!==userUI.reportCategory)userUI.pages.reports=1;
    userUI.reportCategory=category;if(category)path+=`/${category}`;
  }
  if(view==='batch'){
    userUI.projectMode=['groups','single'].includes(parts[1])?parts[1]:null;
    userUI.groupId=parts[1]==='group'&&parts[2]?decodeURIComponent(parts[2]):null;
    userUI.projectId=parts[1]==='project'&&parts[2]?decodeURIComponent(parts[2]):null;
    if(userUI.groupId){projectBatch.batchId=userUI.groupId;path+=`/group/${id(userUI.groupId)}`;}
    else if(userUI.projectId)path+=`/project/${id(userUI.projectId)}`;
    else if(userUI.projectMode)path+=`/${userUI.projectMode}`;
    if(!userUI.groupId&&!projectBatch.busy)projectBatch.batchId='';
  }
  return {view,path};
}
function userBreadcrumb(){
  if(state.view==='overview'&&userUI.taskCategory)return `我的任务 / ${TASK_LABELS[userUI.taskCategory]}`;
  if(state.view==='reports'&&userUI.reportCategory)return `报告 / ${REPORT_LABELS[userUI.reportCategory]}`;
  if(state.view==='batch')return userUI.projectId?'项目组 / 评审历史':userUI.groupId?'项目组 / 组内报告':userUI.projectMode==='groups'?'项目组 / 项目组记录':userUI.projectMode==='single'?'项目组 / 单个项目':NAV.batch;
  return NAV[state.view];
}
function entryCard(title,count,action,icon,tone='teal'){
  return `<button class="entry-card tone-${tone}" ${action}><span class="entry-icon" aria-hidden="true">${icon}</span><span class="entry-label">${esc(title)}</span><strong class="entry-count">${count}</strong><span class="entry-arrow" aria-hidden="true">↗</span></button>`;
}
function localDay(value){
  if(!value)return '';
  const day=new Date(value);
  return `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;
}
function matchesUserFilter(text,createdAt,key){
  const filter=userUI.filters[key],day=localDay(createdAt);
  return String(text).toLocaleLowerCase().includes(filter.query.trim().toLocaleLowerCase())&&(!filter.after||day>=filter.after)&&(!filter.before||day<=filter.before);
}
function userSearch(key,placeholder,withDates=false){
  const filter=userUI.filters[key];
  return `<div class="user-search-bar no-print"><label class="search-field"><span class="search-symbol" aria-hidden="true">⌕</span><input id="user-${key}-query" data-user-filter="${key}" data-filter-field="query" type="search" aria-label="${esc(placeholder)}" placeholder="${esc(placeholder)}" value="${esc(filter.query)}"></label>${withDates?`<div class="date-range"><label>起始日期<input id="user-${key}-after" data-user-filter="${key}" data-filter-field="after" type="date" value="${esc(filter.after)}"></label><span aria-hidden="true">—</span><label>结束日期<input id="user-${key}-before" data-user-filter="${key}" data-filter-field="before" type="date" value="${esc(filter.before)}"></label></div>`:''}${filter.query||filter.after||filter.before?button('重置',`data-user-clear="${key}"`,'subtle'):''}</div>`;
}
function userPageRows(rows,key){
  userUI.pages[key]=Math.min(userUI.pages[key],Math.max(1,Math.ceil(rows.length/USER_PAGE_SIZE)));
  return rows.slice((userUI.pages[key]-1)*USER_PAGE_SIZE,userUI.pages[key]*USER_PAGE_SIZE);
}
function userPagination(total,key){
  const page=userUI.pages[key],pages=Math.max(1,Math.ceil(total/USER_PAGE_SIZE));
  const numbers=[...new Set([1,page-1,page,page+1,pages])].filter(n=>n>=1&&n<=pages).sort((a,b)=>a-b);
  return `<nav class="user-pagination no-print" aria-label="列表分页"><span>共 ${total} 条</span><div class="page-buttons">${button('上一页',`data-user-page="${key}" data-page="${page-1}" ${page===1?'disabled':''}`)}${numbers.map((number,index)=>`${index&&number>numbers[index-1]+1?'<span class="page-gap">…</span>':''}${button(number,`data-user-page="${key}" data-page="${number}" ${number===page?'aria-current="page"':''}`,number===page?'primary':'secondary')}`).join('')}${button('下一页',`data-user-page="${key}" data-page="${page+1}" ${page===pages?'disabled':''}`)}</div></nav>`;
}
function userTaskStatus(row){
  if(row.project.batch_status==='error')return 'error';
  if(!row.run)return 'unreviewed';
  if(row.run.status==='archived')return 'archived';
  return row.run.counts.fail||row.run.counts.pending?'issues':'ready';
}
function matchesTaskCategory(row,category){return category==='all'||(category==='active'?userTaskStatus(row)!=='archived':userTaskStatus(row)===category);}
async function loadUserTasks(){
  const request=++userTasks.request;userTasks.loading=true;userTasks.error='';if(state.view==='overview')render();
  try{
    const [projects,groups]=await Promise.all([api('/api/workbench/project-comparison'),api('/api/workbench/batches')]);
    const grouped=groups.batches.filter(group=>group.project_id),groupedIds=new Set(grouped.map(group=>group.project_id));
    const [standalone,reports]=await Promise.all([
      Promise.all(projects.rows.filter(row=>!groupedIds.has(row.project.id)).map(async row=>({...row,run:await api(`/api/workbench/projects/${id(row.project.id)}/runs/latest`)}))),
      Promise.all(grouped.map(group=>api(`/api/workbench/project-comparison?batch_id=${id(group.id)}`)))
    ]);
    if(request!==userTasks.request)return;
    state.projects=projects.rows.map(row=>row.project);
    userTasks.rows=[...standalone,...reports.flatMap(data=>data.rows)].sort((a,b)=>new Date(b.run?.created_at||b.project.created_at)-new Date(a.run?.created_at||a.project.created_at));
    userTasks.loaded=true;
  }catch(error){if(request===userTasks.request)userTasks.error=error.message;}
  finally{if(request===userTasks.request){userTasks.loading=false;if(state.view==='overview')render();}}
}
function renderUserTasks(){
  const category=userUI.taskCategory;
  const actions=(category?button('← 我的任务','data-view="overview"'):'')+button('上传报告','data-single-report','primary')+button('新建项目组','data-batch-new')+button('刷新','data-user-refresh','subtle');
  const title=heading(category?TASK_LABELS[category]:'我的任务','',actions);
  if(userTasks.error)return title+`<section class="panel empty">${esc(userTasks.error)} ${button('重试','data-user-refresh')}</section>`;
  if(!category)return title+`<section class="entry-grid task-entry-grid">${Object.entries(TASK_LABELS).map(([key,label],index)=>entryCard(label,userTasks.loaded?userTasks.rows.filter(row=>matchesTaskCategory(row,key)).length:'—',`data-view="overview/${key}"`,['◷','＋','!','▤','▣','×','▦'][index],key==='error'?'rose':key==='issues'?'amber':'teal')).join('')}</section>`;
  const search=userSearch('tasks','搜索任务、项目或报告');
  if(userTasks.loading||!userTasks.loaded)return title+search+'<section class="panel empty">正在加载…</section>';
  const rows=userTasks.rows.filter(row=>matchesTaskCategory(row,category)&&matchesUserFilter(`${batchReportName(row.project)} ${row.project.name} ${row.run?.scope_name||''}`,row.project.created_at,'tasks'));
  const content=userPageRows(rows,'tasks').map(row=>{
    const status=userTaskStatus(row),p=row.project,run=row.run;
    const action=status==='error'&&p.batch_id?button('继续处理',`data-user-group="${esc(p.batch_id)}"`,'primary'):run?button(status==='archived'?'查看报告':'处理任务',`data-user-run="${esc(run.id)}" data-user-destination="${status==='archived'?'reports':'review'}"`,'primary'):button(p.batch_id?'继续处理':'上传材料',p.batch_id?`data-user-group="${esc(p.batch_id)}"`:`data-user-materials="${esc(p.id)}"`,'primary');
    return `<article class="panel user-task"><div><div class="row wrap"><h2>${esc(batchReportName(p))}</h2>${badge(status==='error'?'fail':status==='archived'?'archived':'pending',TASK_LABELS[status])}</div><div class="record-meta">${p.item_id?`<span>${esc(projectName(p))}</span>`:run?.material_package_id?`<span>${esc(run.scope_name)}</span>`:''}${run?`<span>${run.counts.fail} 项不符合 · ${run.counts.pending} 项待确认</span>`:''}<time>${esc(date(run?.created_at||p.created_at))}</time></div>${p.error?`<p class="fail-text">${esc(p.error)}</p>`:''}</div><div class="row wrap">${action}${run?button('提问',`data-user-run="${esc(run.id)}" data-user-destination="chat"`):''}</div></article>`;
  }).join('');
  return title+search+`<div class="user-task-list">${content||'<section class="panel empty">暂无任务</section>'}</div>`+userPagination(rows.length,'tasks');
}
function standaloneUserProjects(){
  const groupedIds=new Set(projectBatch.batches.map(group=>group.project_id));
  return state.projects.filter(project=>!groupedIds.has(project.id));
}
function renderUserGroups(){
  if(userUI.projectId)return renderUserProjectHistory();
  if(userUI.groupId)return renderUserGroupReports();
  const mode=userUI.projectMode;
  const title=heading(mode==='groups'?'项目组记录':mode==='single'?'单个项目':'项目组','',(mode?button('← 项目入口','data-view="batch"'):'')+button(mode==='single'?'上传报告':'新建项目组',mode==='single'?'data-single-report':'data-batch-new','primary'));
  if(projectBatch.loading)return title+'<section class="panel empty">正在加载…</section>';
  if(projectBatch.error)return title+`<section class="panel empty">${esc(projectBatch.error)} ${button('重试','data-batch-refresh')}</section>`;
  if(!mode)return title+`<section class="entry-grid project-entry-grid">${entryCard('项目组',projectBatch.batches.length,'data-view="batch/groups"','▦')}${entryCard('单个项目',standaloneUserProjects().length,'data-view="batch/single"','▤','blue')}</section>`;
  const key=mode==='groups'?'groups':'singles';
  const source=mode==='groups'?projectBatch.batches:standaloneUserProjects();
  const rows=source.filter(item=>matchesUserFilter(`${item.name} ${item.id} ${item.specialty||''} ${item.project_type||''}`,item.created_at,key));
  return title+userSearch(key,mode==='groups'?'搜索项目组名称或编号':'搜索项目名称、专业或编号',true)+`<section class="project-record-grid">${userPageRows(rows,key).map(item=>`<article class="panel project-record"><div class="project-record-heading"><span class="project-record-icon" aria-hidden="true">${mode==='groups'?'▦':'▤'}</span><h2>${esc(item.name||projectName(item))}</h2></div><dl class="project-record-meta"><div><dt>建立时间</dt><dd><time>${esc(date(item.created_at))}</time></dd></div><div><dt>${mode==='groups'?'报告数量':'专业'}</dt><dd>${mode==='groups'?`${item.total} 份`:esc(specialty(item.specialty))}</dd></div></dl><div class="row wrap">${mode==='groups'?button('进入项目组',`data-user-group="${esc(item.id)}"`,'primary'):button('评审历史',`data-view="batch/project/${id(item.id)}"`,'primary')+button('项目材料',`data-user-materials="${esc(item.id)}"`)+button('提问',`data-user-chat-project="${esc(item.id)}"`,'subtle')}</div></article>`).join('')||'<section class="panel empty">暂无匹配的项目</section>'}</section>`+userPagination(rows.length,key);
}
function renderUserGroupReports(){
  const group=projectBatch.batches.find(item=>item.id===userUI.groupId);
  const title=heading(group?.name||'项目组','',button('← 项目组记录','data-view="batch/groups"')+(group?.project_id?button('评审历史',`data-view="batch/project/${id(group.project_id)}"`):'')+button('新建项目组','data-batch-new','primary'));
  if(projectBatch.loading)return title+'<section class="panel empty">正在加载…</section>';
  if(projectBatch.error||!group)return title+`<section class="panel empty">${esc(projectBatch.error||'未找到项目组')}</section>`;
  const rows=filteredBatchRows().filter(row=>matchesUserFilter(`${batchReportName(row.project)} ${row.project.filename||''}`,row.run?.created_at||row.project.created_at,'groupReports'));
  const ranked=projectBatch.rows.filter(row=>comparisonScore(row.run)!==null).sort((a,b)=>compareQualityRows(a,b,'score'));
  const ranks=new Map(ranked.map((row,index)=>[batchRowKey(row.project),index+1]));
  const pageRows=userPageRows(rows,'groupReports'),selected=pageRows.find(row=>row.run&&batchRowKey(row.project)===projectBatch.selected)||pageRows.find(row=>row.run);
  const controls=userSearch('groupReports','搜索组内报告',true)+`<div class="list-actions"><div class="row wrap"><label>评审结果 <select id="project-batch-filter">${options({all:'全部',pass:'符合',fail:'不符合',pending:'待确认',unreviewed:'未评审',error:'处理失败'},projectBatch.filter)}</select></label><label>排序方式 <select id="project-batch-sort">${options({score:'参考分由高到低',fail:'不符合项由少到多',pending:'待确认项由少到多',time:'评审时间由新到旧'},projectBatch.sort)}</select></label></div><div class="row wrap">${button('刷新','data-batch-refresh')}${button('继续未完成报告',`data-batch-resume ${projectBatch.busy?'disabled':''}`)}${group.project_id?button('项目材料',`data-user-materials="${esc(group.project_id)}"`):''}</div></div>`;
  const table=`<section class="panel"><div class="table-scroll"><table class="user-group-table"><thead><tr><th>排名</th><th>报告</th><th>评分</th><th>进度</th><th>评审结果</th><th>评审时间</th><th>操作</th></tr></thead><tbody>${pageRows.map(row=>{
    const p=row.project,key=batchRowKey(p),run=row.run;
    return `<tr class="${selected===row?'comparison-selected':''}"><td><span class="user-rank">${ranks.get(key)||'—'}</span></td><td>${run?button(esc(batchReportName(p)),`data-batch-score="${esc(key)}"`,'subtle user-score-report'):`<strong>${esc(batchReportName(p))}</strong>`}</td><td>${userScoreValue(run)}</td><td>${esc(BATCH_STATUS[p.batch_status]||(run?'评审完成':'未评审'))}${p.error?`<p class="fail-text">${esc(p.error)}</p>`:''}</td><td>${run?badge(conclusionStyle(run),runTitle(run)):'—'}</td><td><time>${esc(date(run?.created_at))}</time></td><td><div class="batch-row-actions">${run?button('查看报告',`data-user-run="${esc(run.id)}" data-user-destination="reports"`)+button('处理问题',`data-user-run="${esc(run.id)}"`)+button('提问',`data-user-run="${esc(run.id)}" data-user-destination="chat"`):''}${p.batch_id?button(p.batch_status==='error'?'重试':run?'重新评审':'继续处理',`data-batch-retry="${esc(key)}" ${projectBatch.busy?'disabled':''}`):''}${p.batch_id&&!p.uploaded?`<label class="batch-replace">选择报告<input type="file" accept=".docx,.doc,.pdf,.txt" data-batch-file="${esc(key)}" ${projectBatch.busy?'disabled':''}></label>`:''}</div></td></tr>`;
  }).join('')||'<tr><td colspan="7" class="empty">暂无匹配的报告</td></tr>'}</tbody></table></div></section>`;
  return title+controls+(projectBatch.progress?`<div class="notice" role="status">${esc(projectBatch.progress)}</div>`:'')+table+userPagination(rows.length,'groupReports')+userScoreAnalysis(selected?.run,{rows:ranked});
}
async function loadUserProjectHistory(){
  const projectId=userUI.projectId,request=++userProjectHistory.request;
  userProjectHistory.projectId=projectId;userProjectHistory.loading=true;userProjectHistory.error='';render();
  try{
    const [data,groups]=await Promise.all([api(`/api/workbench/projects/${id(projectId)}/runs`),api('/api/workbench/batches')]);
    if(request!==userProjectHistory.request)return;
    const project=state.projects.find(item=>item.id===projectId)||{id:projectId,name:projectId};
    projectBatch.batches=groups.batches;
    userProjectHistory.runs=data.runs.map(run=>({...run,project}));
  }catch(error){if(request===userProjectHistory.request)userProjectHistory.error=error.message;}
  finally{if(request===userProjectHistory.request){userProjectHistory.loading=false;if(state.view==='batch'&&userUI.projectId===projectId)render();}}
}
function reportName(run){return run.scope_name&&run.scope_name!=='整个项目'?run.scope_name:projectName(run.project);}
function reportText(run){return `${reportName(run)} ${run.project.name} ${run.id} ${runTitle(run)}`;}
function userReportRows(rows,inline=true){
  return `<div class="history-list">${rows.map(run=>`<section class="panel history-record"><button class="history-summary" ${inline?`data-expand-report="${esc(run.id)}" aria-expanded="${expandedReport?.id===run.id}" aria-controls="history-${esc(run.id)}"`:`data-user-run="${esc(run.id)}" data-user-destination="reports"`}><span><strong>${esc(reportName(run))}</strong><span class="record-meta"><span>${esc(projectName(run.project))}</span><time>${esc(date(run.created_at))}</time></span></span><span class="row wrap">${badge(run.qualification?'pass':conclusionStyle(run),run.qualification?'合格 · 人工确认':runTitle(run))}${run.status==='archived'?'<span class="pill archived">已归档</span>':''}<span class="record-chevron" aria-hidden="true">${inline&&expandedReport?.id===run.id?'⌃':'›'}</span></span></button>${inline&&expandedReport?.id===run.id?`<div class="history-content" id="history-${esc(run.id)}"><div class="row wrap no-print">${button('处理问题',`data-user-run="${esc(run.id)}"`)}${button('提问',`data-user-run="${esc(run.id)}" data-user-destination="chat"`)}</div>${reportContent(expandedReport)}</div>`:''}</section>`).join('')||'<section class="panel empty">暂无匹配的报告</section>'}</div>`;
}
function renderUserProjectHistory(){
  const project=state.projects.find(item=>item.id===userUI.projectId),group=projectBatch.batches.find(item=>item.project_id===userUI.projectId);
  const title=heading(project?projectName(project):'评审历史','',button('← 返回',`data-view="${group?`batch/group/${id(group.id)}`:'batch/single'}"`)+button('项目材料',`data-user-materials="${esc(userUI.projectId)}"`));
  if(userProjectHistory.loading||userProjectHistory.projectId!==userUI.projectId)return title+'<section class="panel empty">正在加载…</section>';
  if(userProjectHistory.error)return title+`<section class="panel empty">${esc(userProjectHistory.error)}</section>`;
  const rows=userProjectHistory.runs.filter(run=>matchesUserFilter(reportText(run),run.created_at,'projectHistory'));
  return title+`<div class="record-meta project-created">建立时间 <time>${esc(date(project?.created_at))}</time></div>`+userSearch('projectHistory','搜索历史报告',true)+userReportRows(userPageRows(rows,'projectHistory'),false)+userPagination(rows.length,'projectHistory');
}
function matchesReportCategory(run,category){
  if(category==='archived')return run.status==='archived';
  const passed=run.result==='pass'||run.qualification?.result==='qualified';
  return category==='all'||(category==='pass'?passed:!passed);
}
function renderUserReports(){
  const category=userUI.reportCategory;
  const title=heading(category?REPORT_LABELS[category]:'报告','',(category?button('← 报告分类','data-view="reports"'):'')+button('刷新','data-refresh-history','subtle'));
  const search=userSearch('reports','搜索报告名称、项目或编号');
  if(passedReports?.error)return title+search+'<section class="panel empty">报告加载失败</section>';
  const matching=(passedReports||[]).filter(run=>matchesUserFilter(reportText(run),run.created_at,'reports'));
  if(!category)return title+search+`<section class="entry-grid report-entry-grid">${Object.entries(REPORT_LABELS).map(([key,label],index)=>entryCard(label,passedReports?matching.filter(run=>matchesReportCategory(run,key)).length:'—',`data-view="reports/${key}"`,['▣','✓','!','▤'][index],['slate','teal','amber','blue'][index])).join('')}</section>`;
  if(passedReports===null)return title+search+'<section class="panel empty">正在加载…</section>';
  const rows=matching.filter(run=>matchesReportCategory(run,category));
  return title+search+userReportRows(userPageRows(rows,'reports'))+userPagination(rows.length,'reports');
}
async function loadUserReportChoices(){
  if(userUI.chatLoading)return;
  userUI.chatLoading=true;if(state.view==='chat')render();
  try{
    await loadPassedReports();
    if(!userUI.chatRestored&&state.view==='chat'){
      userUI.chatRestored=true;
      const remembered=localStorage.getItem('review-workbench-selected-report');
      if(Array.isArray(passedReports)&&passedReports.some(run=>run.id===remembered)&&state.run?.id!==remembered)await openUserRun(remembered,'chat');
    }
  }
  finally{userUI.chatLoading=false;if(state.view==='chat')render();}
}
function userContextPicker(){
  const run=state.run,open=userUI.chatOpen||!run;
  const selected=`<div class="selected-report"><div><span class="selected-report-label">当前报告</span><strong>${run?esc(reportName(run)):'未选择'}</strong>${run?`<div class="record-meta"><span>${esc(projectName(run.project))}</span><time>${esc(date(run.created_at))}</time></div>`:''}</div><div class="row wrap">${button(open?'收起':'切换报告',`data-user-report-picker aria-expanded="${open}" aria-controls="user-report-picker"`,'primary')}${run?button('处理问题','data-view="review"')+button('查看报告','data-current-report'):button('上传报告','data-single-report')}</div></div>`;
  if(!open)return `<section class="panel user-report-context">${selected}</section>`;
  const rows=(Array.isArray(passedReports)?passedReports:[]).filter(item=>matchesUserFilter(reportText(item),item.created_at,'chat'));
  return `<section class="panel user-report-context">${selected}<div id="user-report-picker" class="report-picker">${userSearch('chat','搜索报告名称、项目或编号')}${userUI.chatLoading?'<div class="empty">正在加载…</div>':passedReports?.error?`<div class="empty">报告加载失败 ${button('重试','data-user-refresh-choices')}</div>`:`<div class="report-options" role="list" aria-label="可选报告">${userPageRows(rows,'chat').map(item=>`<button class="report-option ${item.id===run?.id?'selected':''}" data-user-run="${esc(item.id)}" data-user-destination="chat" ${agentChat.busy?'disabled':''}><span><strong>${esc(reportName(item))}</strong><span class="record-meta"><span>${esc(projectName(item.project))}</span><time>${esc(date(item.created_at))}</time></span></span><span aria-hidden="true">${item.id===run?.id?'✓':'›'}</span></button>`).join('')||'<div class="empty">暂无匹配的报告</div>'}</div>${userPagination(rows.length,'chat')}`}</div></section>`;
}
function renderUserChat(){return heading('评审对话','')+userContextPicker()+(state.run?'<div id="user-agent-anchor"></div>':'');}
function renderUserReview(){
  if(!state.project)return noProject();
  const title=heading(projectName(state.project),'',button('← 我的任务',`data-view="${userUI.taskCategory?`overview/${userUI.taskCategory}`:'overview'}"`)+button('提问','data-view="chat"'));
  const controls=`<div class="user-review-controls"><div class="segmented">${[['material','材料'],['decision','问题与复核'],['report','报告']].map(([key,label])=>button(label,key==='report'?'data-current-report':`data-step="${key}"`,state.step===key?'active':'')).join('')}</div>${button(state.busy?'评审中…':state.run?'重新评审':'开始评审',`data-run ${state.busy?'disabled':''}`,'primary')}</div>`;
  const brief=state.run?`<section class="panel user-result"><div class="row spread wrap"><div class="row wrap"><h2>${esc(reviewProgressTitle(state.run))}</h2>${badge(conclusionStyle(state.run),runTitle(state.run))}</div>${userScoreValue(state.run)}</div><div class="record-meta"><span>${esc(state.run.scope_name)}</span><span>${state.run.counts.pass} 项符合 · ${state.run.counts.fail} 项不符合 · ${state.run.counts.pending} 项待确认</span>${state.run.status==='archived'?'<span class="pill archived">已归档</span>':''}</div></section>`:'';
  const content=state.step==='material'?materialsContent():state.step==='report'?reportContent():state.run?checksContent():`<section class="panel empty">${button('上传材料','data-upload','primary')}</section>`;
  return title+controls+staleNotice()+brief+(state.run&&state.step==='decision'?userScoreAnalysis(state.run):'')+`<div class="review-content gap">${content}</div>`;
}
function renderUserMaterials(){
  if(!state.project)return heading('项目材料','',button('选择项目','data-view="batch/single"','primary'));
  const group=projectBatch.batches.find(item=>item.project_id===state.project.id);
  return heading(projectName(state.project),'',button('← 返回项目',`data-view="${group?`batch/group/${id(group.id)}`:'batch/single'}"`)+button('进入评审','data-view="review"','primary'))+materialsContent();
}
function stripUserHints(root){root.querySelectorAll('.brief-upload > small,.brief-box small,.modal-body > p.muted,.review-entries p').forEach(note=>note.remove());}
function renderUserHelp(){return heading('使用指南','')+`<section class="panel panel-body"><ol class="user-help-steps"><li>我的任务：选择状态，查看并处理任务。</li><li>项目组：选择项目组或单个项目，查看材料与评审历史。</li><li>报告：选择分类，搜索并导出报告。</li><li>评审对话：搜索报告，选择后开始提问。</li></ol></section>`;}
async function openUserRun(runId,destination='review'){
  const run=await api(`/api/workbench/runs/${id(runId)}`);
  await selectProject(run.project_id,null);
  if(state.project?.id!==run.project_id)return;
  state.run=run;state.scope=run.material_package_id;state.step='decision';
  state.rules=(await api(`/api/workbench/rules?project_id=${id(run.project_id)}${state.scope!==null?'&material_package_id='+id(state.scope):''}`)).rules;
  if(destination==='reports')return openUserReport(run);
  await loadUserRanking(run);
  if(destination==='chat'){userUI.chatRestored=true;localStorage.setItem('review-workbench-selected-report',run.id);}
  userUI.chatOpen=false;navigate(destination);
}
async function openUserReport(run=state.run){
  userUI.filters.reports.query='';
  if(run)await loadUserRanking(run);
  await navigate('reports/all');
  if(state.view==='reports'&&run){
    const index=Array.isArray(passedReports)?passedReports.findIndex(item=>item.id===run.id):-1;
    userUI.pages.reports=Math.floor(Math.max(0,index)/USER_PAGE_SIZE)+1;
    expandedReport=run;render();$('.history-content')?.scrollIntoView({block:'start'});
  }
}
document.addEventListener('click',event=>{
  const b=event.target.closest('button');if(!b)return;
  guarded(async()=>{
    if(b.hasAttribute('data-user-refresh'))return loadUserTasks();
    if(b.hasAttribute('data-user-refresh-choices'))return loadUserReportChoices();
    if(b.dataset.userMaterials)return selectProject(b.dataset.userMaterials,'projects');
    if(b.dataset.userChatProject){userUI.chatRestored=true;await selectProject(b.dataset.userChatProject,'chat');if(state.run)localStorage.setItem('review-workbench-selected-report',state.run.id);return;}
    if(b.dataset.userRun)return openUserRun(b.dataset.userRun,b.dataset.userDestination||'review');
    if(b.dataset.userGroup){
      if(projectBatch.busy&&projectBatch.batchId!==b.dataset.userGroup)return toast('当前项目组正在处理');
      projectBatch.search='';projectBatch.filter='all';userUI.pages.groupReports=1;
      return navigate(`batch/group/${id(b.dataset.userGroup)}`);
    }
    if(b.hasAttribute('data-user-report-picker')){userUI.chatOpen=!(userUI.chatOpen||!state.run);render();if(userUI.chatOpen)$('#user-chat-query')?.focus();}
    if(b.dataset.userPage){userUI.pages[b.dataset.userPage]=Number(b.dataset.page);expandedReport=null;render();if(b.dataset.userPage!=='chat')window.scrollTo({top:0});}
    if(b.dataset.userClear){userUI.filters[b.dataset.userClear]={query:'',after:'',before:''};userUI.pages[b.dataset.userClear]=1;expandedReport=null;render();}
  });
});
function updateUserFilter(input){
  if(!input.dataset.userFilter)return;
  const key=input.dataset.userFilter,field=input.dataset.filterField,position=input.selectionStart;
  userUI.filters[key][field]=input.value;userUI.pages[key]=1;expandedReport=null;
  render();const replacement=document.getElementById(input.id);replacement?.focus();
  if(field==='query')replacement?.setSelectionRange(position,position);
}
document.addEventListener('input',event=>updateUserFilter(event.target));
document.addEventListener('change',event=>{if(event.target.type==='date')updateUserFilter(event.target);});

'use strict';

const ADMIN_RECORD_TYPES={all:'全部记录',materials:'项目与材料',reviews:'评审记录',manual:'人工处理',conversations:'对话记录',system:'系统管理'};
const ADMIN_STATUS={success:'已完成',error:'失败',running:'处理中'};
const adminUI={category:'',kind:'',projectId:'',loading:false,error:'',request:0,overview:null,accounts:[],batches:[],detail:null,records:null,projectPage:1,timelinePage:1,historyPage:1,
  filters:{query:'',username:'',after:'',before:'',status:'',report_state:''},projectFilters:{query:'',username:'',after:'',before:''},recordPage:1};

function applyAdminRoute(path){
  const parts=String(path||'overview').split('/').map(decodeURIComponent);
  adminUI.category=parts[0]==='reports'&&ADMIN_RECORD_TYPES[parts[1]]?parts[1]:'';
  adminUI.kind=parts[0]==='projects'&&['groups','single','detail','legacy'].includes(parts[1])?parts[1]:'';
  const projectId=['detail','legacy'].includes(adminUI.kind)?parts[2]||'':'';
  if(adminUI.projectId!==projectId){adminUI.timelinePage=1;adminUI.historyPage=1;adminUI.detail=null;}
  adminUI.projectId=projectId;
  const view=NAV[parts[0]]?parts[0]:'overview';
  return {view,path:view==='reports'&&adminUI.category?`reports/${adminUI.category}`:view==='projects'&&adminUI.kind?`projects/${adminUI.kind}${adminUI.projectId?'/'+id(adminUI.projectId):''}`:view};
}
const adminOwnerOptions=(projects=false)=>({'':'全部用户',legacy:projects?'历史项目':'历史数据',...Object.fromEntries(adminUI.accounts.filter(a=>a.role==='user').map(a=>[a.username,a.username]))});
function adminFilterForm(projects=false){
  const f=projects?adminUI.projectFilters:adminUI.filters;
  return `<form id="admin-${projects?'projects':'records'}-filter" class="admin-filters panel"><label class="admin-search"><span class="sr-only">关键词</span><input name="query" type="search" placeholder="${projects?'搜索项目名称':'搜索项目、报告或操作'}" value="${esc(f.query)}"></label><label><span class="sr-only">用户</span><select name="username" aria-label="按用户筛选">${options(adminOwnerOptions(projects),f.username)}</select></label><label class="admin-date">从<input name="after" aria-label="起始日期" type="date" value="${esc(f.after)}"></label><label class="admin-date">至<input name="before" aria-label="结束日期" type="date" value="${esc(f.before)}"></label>${projects?'':`<select name="status" aria-label="执行结果">${options({'':'全部执行结果',...ADMIN_STATUS},f.status)}</select><select name="report_state" aria-label="报告状态">${options({'':'全部报告状态',issues:'待处理',pass:'已通过／人工确认',archived:'已归档'},f.report_state)}</select>`}<button class="primary" type="submit">搜索</button>${button('重置',`type="button" data-admin-reset="${projects?'projects':'records'}"`,'subtle')}</form>`;
}
function adminPager(total,page,key,size=20){
  const pages=Math.max(1,Math.ceil(total/size));
  return `<div class="user-pagination"><span>共 ${total} 条</span><div class="row"><button data-admin-page="${key}" data-page="${page-1}" ${page<=1?'disabled':''}>上一页</button><span>${page} / ${pages}</span><button data-admin-page="${key}" data-page="${page+1}" ${page>=pages?'disabled':''}>下一页</button></div></div>`;
}
function adminRecordTable(rows){
  return `<div class="table-scroll"><table class="admin-record-table"><thead><tr><th>时间</th><th>操作人</th><th>项目／报告</th><th>操作</th><th>执行结果</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr><td><time>${esc(date(r.created_at))}</time></td><td>${esc(r.username||'历史数据')}</td><td><strong>${esc(r.title||r.project_name||'—')}</strong>${r.project_name&&r.project_name!==r.title?`<span class="admin-secondary">${esc(r.project_name)}</span>`:''}</td><td>${esc(r.action)}</td><td>${badge(r.status==='error'?'fail':r.status==='running'?'pending':'pass',ADMIN_STATUS[r.status]||r.status)}</td><td>${button('详情',`data-admin-record="${r.id}"`,'subtle')}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">暂无匹配记录</td></tr>'}</tbody></table></div>`;
}
function adminLoading(){return `<section class="panel empty">${adminUI.error?esc(adminUI.error):'正在加载…'}${adminUI.error?button('重试','data-admin-refresh'):''}</section>`;}
async function loadAdminView(){
  const request=++adminUI.request,view=state.view,projectId=adminUI.projectId;
  adminUI.loading=true;adminUI.error='';render();
  try{
    const accounts=await api('/api/admin/accounts');
    if(request!==adminUI.request)return;
    adminUI.accounts=accounts.accounts;
    if(view==='overview'){
      const data=await api('/api/admin/overview');if(request!==adminUI.request)return;adminUI.overview=data;
    }else if(view==='projects'){
      const [projects,batches]=await Promise.all([api('/api/projects'),api('/api/workbench/batches')]);
      if(request!==adminUI.request)return;state.projects=projects.projects;adminUI.batches=batches.batches;
      if(adminUI.kind==='legacy'){
        const data=await api(`/api/workbench/project-comparison?batch_id=${id(projectId)}`);
        if(request!==adminUI.request)return;adminUI.detail={legacyRows:data.rows};
      }else if(projectId){
        const [material,history,records]=await Promise.all([api(`/api/workbench/projects/${id(projectId)}/materials`),api(`/api/workbench/projects/${id(projectId)}/runs`),api(`/api/admin/records?project_id=${id(projectId)}&page_size=20&page=${adminUI.timelinePage}`)]);
        if(request!==adminUI.request)return;adminUI.detail={material,history:history.runs,records};adminUI.timelinePage=records.page;
      }
    }else if(view==='reports'){
      const params=new URLSearchParams({...adminUI.filters,category:adminUI.category||'all',page:adminUI.recordPage});
      const data=await api(`/api/admin/records?${params}`);if(request!==adminUI.request)return;adminUI.records=data;adminUI.recordPage=data.page;
    }
  }catch(error){if(request===adminUI.request)adminUI.error=error.message;}
  finally{if(request===adminUI.request){adminUI.loading=false;render();}}
}
function renderAdminOverview(){
  const title=heading('管理概览','',button('刷新','data-admin-refresh','subtle'));
  if(adminUI.loading||adminUI.error||!adminUI.overview)return title+adminLoading();
  const o=adminUI.overview;
  return title+`<section class="admin-metrics">${[['项目总数',o.projects,'projects'],['评审报告',o.reports,'reports/reviews'],['待处理报告',o.pending,'reports/reviews'],['失败记录',o.errors,'reports/all']].map(([label,value,view])=>`<button class="panel admin-metric" data-admin-metric="${view}" ${label==='失败记录'?'data-error-only':label==='待处理报告'?'data-pending-only':''}><span>${label}</span><strong>${value}</strong><span class="admin-metric-arrow">↗</span></button>`).join('')}</section><section class="admin-shortcuts">${[['项目管理','查看项目与分配归属','projects'],['评审规则','维护评审依据','rules'],['模型配置','管理评审模型','models']].map(([name,desc,view])=>`<button class="panel" data-view="${view}"><strong>${name}</strong><span aria-hidden="true">→</span></button>`).join('')}</section>`+panel('近期活动','',adminRecordTable(o.recent),button('全部记录','data-view="reports/all"','subtle'));
}
function renderAdminProjects(){
  const kind=adminUI.kind,title=heading(kind==='detail'?'项目详情':kind==='groups'?'项目组':kind==='single'?'单个项目':'项目管理','',kind?button('← 返回',`data-view="${kind==='detail'?'projects/'+(adminUI.batches.some(b=>b.project_id===adminUI.projectId)?'groups':'single'):'projects'}"`):button('刷新','data-admin-refresh','subtle'));
  if(adminUI.loading||adminUI.error)return title+adminLoading();
  const grouped=new Set(adminUI.batches.map(b=>b.project_id).filter(Boolean));
  if(kind==='legacy'){
    const group=adminUI.batches.find(b=>b.id===adminUI.projectId);
    return heading(group?.name||'历史项目组','',button('← 项目组','data-view="projects/groups"'))+`<section class="panel"><div class="table-scroll"><table><thead><tr><th>项目／报告</th><th>结论</th><th>操作</th></tr></thead><tbody>${(adminUI.detail?.legacyRows||[]).map(row=>`<tr><td>${esc(projectName(row.project))}</td><td>${row.run?badge(conclusionStyle(row.run),runTitle(row.run)):'未评审'}</td><td>${button('查看项目',`data-view="projects/detail/${id(row.project.id)}"`,'subtle')}</td></tr>`).join('')||'<tr><td colspan="3" class="empty">暂无项目</td></tr>'}</tbody></table></div></section>`;
  }
  if(!kind)return title+`<section class="entry-grid project-entry-grid">${[['groups','项目组',adminUI.batches.length,'▦'],['single','单个项目',state.projects.filter(p=>!grouped.has(p.id)).length,'▤']].map(([key,name,count,icon])=>`<button class="entry-card" data-view="projects/${key}"><span class="entry-icon">${icon}</span><span class="entry-copy"><strong>${name}</strong><span>${count} 个项目</span></span><span class="entry-arrow">→</span></button>`).join('')}</section>`;
  if(kind==='detail')return title+renderAdminProjectDetail();
  const f=adminUI.projectFilters,words=f.query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const source=kind==='groups'?[...state.projects.filter(p=>grouped.has(p.id)),...adminUI.batches.filter(b=>!b.project_id).map(b=>({...b,legacyBatch:true}))]:state.projects.filter(p=>!grouped.has(p.id));
  const rows=source.filter(p=>(!f.username||(p.owner||'legacy')===f.username)&&(!f.after||localAdminDay(p.created_at)>=f.after)&&(!f.before||localAdminDay(p.created_at)<=f.before)&&words.every(w=>`${projectName(p)} ${specialty(p.specialty)} ${projectType(p.project_type)}`.toLocaleLowerCase().includes(w)));
  adminUI.projectPage=Math.min(adminUI.projectPage,Math.max(1,Math.ceil(rows.length/20)));
  return title+adminFilterForm(true)+`<section class="panel"><div class="table-scroll"><table><thead><tr><th>项目名称</th><th>所属用户</th><th>专业</th><th>建立时间</th><th>操作</th></tr></thead><tbody>${rows.slice((adminUI.projectPage-1)*20,adminUI.projectPage*20).map(p=>`<tr><td><strong>${esc(projectName(p))}</strong></td><td>${esc(p.owner||'历史项目')}</td><td>${esc(specialty(p.specialty))}</td><td>${esc(date(p.created_at))}</td><td><div class="row">${button('查看',`data-view="projects/${p.legacyBatch?'legacy':'detail'}/${id(p.id)}"`,'subtle')}${p.legacyBatch?'':button('分配',`data-admin-assign="${esc(p.id)}"`,'subtle')}</div></td></tr>`).join('')||'<tr><td colspan="5" class="empty">暂无匹配项目</td></tr>'}</tbody></table></div></section>`+adminPager(rows.length,adminUI.projectPage,'projects');
}
function localAdminDay(value){return new Date(value).toLocaleDateString('en-CA');}
function renderAdminProjectDetail(){
  const d=adminUI.detail;if(!d)return adminLoading();
  const p=state.projects.find(p=>p.id===adminUI.projectId)||d.material.project;
  return `<section class="panel panel-body admin-project-summary"><div><span class="admin-secondary">${esc(p.owner||'历史项目')}</span><h2>${esc(projectName(p))}</h2><div class="record-meta"><span>${esc(specialty(p.specialty))}</span><time>${esc(date(p.created_at))}</time></div></div>${button('分配所属用户',`data-admin-assign="${esc(p.id)}"`)}</section>`+
    panel('项目材料','',`<div class="panel-body">${d.material.sources.map(s=>`<div class="admin-material"><a href="${esc(API)}/api/projects/${id(p.id)}/sources/${s.id}/visual-original" target="_blank" rel="noopener">${esc(s.filename)}</a>${badge(s.parse_status==='parsed'?'pass':'pending',s.parse_status==='parsed'?'已解析':'待处理')}</div>`).join('')||'<div class="empty">暂无材料</div>'}${d.material.brief?`<details class="admin-brief"><summary>任务要求</summary><p>${esc(d.material.brief)}</p></details>`:''}</div>`)+
    panel('全部评审报告','',`<div class="table-scroll"><table><thead><tr><th>报告</th><th>评审时间</th><th>结论</th><th>归档状态</th><th></th></tr></thead><tbody>${d.history.slice((adminUI.historyPage-1)*20,adminUI.historyPage*20).map(r=>`<tr><td>${esc(r.scope_name==='整个项目'?projectName(p):r.scope_name)}</td><td>${esc(date(r.created_at))}</td><td>${badge(conclusionStyle(r),r.qualification?'合格 · 人工确认':runTitle(r))}</td><td>${r.status==='archived'?'已归档':'未归档'}</td><td>${button('查看报告',`data-admin-run="${esc(r.id)}"`,'subtle')}</td></tr>`).join('')||'<tr><td colspan="5" class="empty">暂无评审报告</td></tr>'}</tbody></table></div>`)+adminPager(d.history.length,adminUI.historyPage,'history')+panel('工作时间线','',adminRecordTable(d.records.records))+adminPager(d.records.total,d.records.page,'timeline');
}
function renderAdminRecords(){
  const category=adminUI.category,title=heading(category?ADMIN_RECORD_TYPES[category]:'工作记录','',category?button('← 记录分类','data-view="reports"'):button('刷新','data-admin-refresh','subtle'));
  const controls=adminFilterForm();
  if(adminUI.loading||adminUI.error||!adminUI.records)return title+controls+adminLoading();
  const data=adminUI.records;
  if(!category)return title+controls+`<section class="entry-grid admin-record-entries">${Object.entries(ADMIN_RECORD_TYPES).map(([key,name],i)=>`<button class="entry-card" data-view="reports/${key}"><span class="entry-icon">${['▥','▤','✓','✎','◌','⚙'][i]}</span><span class="entry-copy"><strong>${name}</strong><span>${data.counts[key]} 条记录</span></span><span class="entry-arrow">→</span></button>`).join('')}</section>`;
  return title+controls+`<section class="panel">${adminRecordTable(data.records)}</section>`+adminPager(data.total,data.page,'records');
}
function renderAdminAccounts(){
  const title=heading('账号管理','',button('刷新','data-admin-refresh','subtle'));
  if(adminUI.loading||adminUI.error)return title+adminLoading();
  return title+`<section class="panel"><div class="table-scroll"><table><thead><tr><th>账号</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>${adminUI.accounts.map(a=>`<tr><td><strong>${esc(a.username)}</strong></td><td>${a.role==='admin'?'管理员':'用户'}</td><td>${badge(a.enabled?'pass':'pending',a.enabled?'已启用':'已停用')}</td><td><div class="row">${button('重置密码',`data-admin-password="${esc(a.username)}"`,'subtle')}${a.role==='user'?button(a.enabled?'停用':'启用',`data-admin-enabled="${esc(a.username)}" data-enabled="${a.enabled?'0':'1'}"`,'subtle'):''}</div></td></tr>`).join('')}</tbody></table></div></section>`;
}
function renderAdminHelp(){
  return heading('管理员使用指南','')+`<section class="panel panel-body"><ol class="user-help-steps"><li>项目管理：选择项目组或单个项目，查看材料、所有评审报告与工作时间线；通过“分配”设置所属用户。</li><li>工作记录：先选择记录类别，再按关键词、用户、日期和执行结果筛选；点击详情查看修改内容、报告或完整会话。</li><li>评审规则：维护评审依据；已有报告保留当时的规则版本，用户重新评审后应用新规则。</li><li>模型配置：选择评审模型、测试连接；模型服务密钥由现有服务端配置管理。</li><li>账号管理：启用或停用用户、重置密码；重置后该账号需要重新登录。</li><li>日常评审：通过右上角或左下角切换账号，登录用户工作台执行上传、评审与人工处理。</li></ol></section>`;
}
function adminChangeDetails(detail){
  const value=v=>typeof v==='boolean'?(v?'是':'否'):Array.isArray(v)?v.join('、'):String(v??'—');
  let html='';
  if(detail.message)html+=`<p>${esc(detail.message)}</p>`;
  if(detail.result)html+=`<p>评审结论：${esc(RESULT[detail.result]||detail.result)}</p>`;
  if(detail.owner!==undefined)html+=`<p>分配给：${esc(detail.owner||'历史项目')}</p>`;
  if(detail.model)html+=`<p>模型：${esc(detail.model)}</p>`;
  if(detail.password_reset)html+='<p>已重置密码</p>';
  if(detail.enabled!==undefined&&detail.enabled!==null)html+=`<p>账号状态：${detail.enabled?'启用':'停用'}</p>`;
  if(typeof detail.before==='string')html+=`<div class="admin-change-grid"><section><h3>修改前</h3><p>${esc(detail.before||'未填写')}</p></section><section><h3>修改后</h3><p>${esc(detail.after||'未填写')}</p></section></div>`;
  else if(detail.before&&detail.after){
    const labels={name:'规则名称',standard:'判断标准',content:'规则内容',enabled:'是否启用',terms:'检索词',suggestion:'修改建议',mode:'执行方式',specialty:'适用专业',version:'版本',result:'结论',status:'状态',model:'模型',url:'服务地址'};
    html+=`<div class="table-scroll"><table><thead><tr><th>内容</th><th>修改前</th><th>修改后</th></tr></thead><tbody>${Object.entries(labels).filter(([k])=>JSON.stringify(detail.before[k])!==JSON.stringify(detail.after[k])).map(([key,label])=>`<tr><td>${label}</td><td>${esc(RESULT[detail.before[key]]||value(detail.before[key]))}</td><td>${esc(RESULT[detail.after[key]]||value(detail.after[key]))}</td></tr>`).join('')}</tbody></table></div>`;
  }
  if(!detail.before&&detail.after?.standard)html+=`<h3>${esc(detail.after.name)}</h3><p>${esc(detail.after.standard)}</p><p>${esc(detail.after.content)}</p>`;
  for(const c of detail.changes||[])html+=`<section class="admin-change"><h3>${esc(c.name)}</h3><div class="admin-change-grid">${[['修改前',c.before],['修改后',c.after]].map(([label,v])=>`<section><h4>${label}</h4><p>${esc(RESULT[v.result]||v.result)}</p><p>${esc(v.reason)}</p><p>${esc(v.suggestion)}</p></section>`).join('')}</div></section>`;
  return html;
}
async function openAdminRecord(recordId){
  const r=await api(`/api/admin/records/${id(recordId)}`);
  const messages=r.conversation?.messages||[];
  modal(r.title||r.action,`<dl class="admin-detail-meta"><div><dt>操作人</dt><dd>${esc(r.username||'历史数据／操作人未记录')}</dd></div><div><dt>时间</dt><dd>${esc(date(r.created_at))}</dd></div><div><dt>操作</dt><dd>${esc(r.action)}</dd></div><div><dt>执行结果</dt><dd>${esc(ADMIN_STATUS[r.status]||r.status)}</dd></div></dl>${adminChangeDetails(r.detail)}${messages.length?`<div class="admin-conversation">${messages.map(m=>`<section class="agent-message ${esc(m.role)}"><strong>${m.role==='user'?'用户':'评审助手'} · ${esc(date(m.at))}</strong><p>${esc(m.content)}</p></section>`).join('')}</div>`:''}`,button('关闭','data-close')+(r.project_id?button('查看项目',`data-admin-record-project="${esc(r.project_id)}"`):'')+(r.run_id?button('查看报告',`data-admin-run="${esc(r.run_id)}"`,'primary'):''));
  $('#editor').classList.add('admin-drawer');
}
async function openAdminRun(runId){
  const r=await api(`/api/workbench/runs/${id(runId)}`);
  const content=document.createElement('div');content.innerHTML=reportContent(r);
  content.querySelectorAll('.report-retention,.report-toolbar,button').forEach(el=>el.remove());
  if($('#editor').open)$('#editor').close();
  modal(r.scope_name==='整个项目'?projectName(r.project):r.scope_name,content.innerHTML,button('关闭','data-close')+button('导出 Word',`data-admin-export="${esc(r.id)}"`,'primary'));
  $('#editor').classList.add('admin-drawer');
}
document.addEventListener('click',event=>{
  if(document.body.dataset.workspace!=='admin')return;
  const b=event.target.closest('button');if(!b)return;
  guarded(async()=>{
    if(b.hasAttribute('data-admin-refresh'))return loadAdminView();
    if(b.dataset.adminMetric){Object.keys(adminUI.filters).forEach(k=>adminUI.filters[k]='');adminUI.recordPage=1;adminUI.filters.status=b.hasAttribute('data-error-only')?'error':'';adminUI.filters.report_state=b.hasAttribute('data-pending-only')?'issues':'';return navigate(b.dataset.adminMetric);}
    if(b.dataset.adminRecord)return openAdminRecord(b.dataset.adminRecord);
    if(b.dataset.adminRun)return openAdminRun(b.dataset.adminRun);
    if(b.dataset.adminRecordProject){$('#editor').close();return navigate(`projects/detail/${id(b.dataset.adminRecordProject)}`);}
    if(b.dataset.adminExport){
      const response=await fetch(`${API}/api/workbench/runs/${id(b.dataset.adminExport)}/report.docx`);if(!response.ok)throw new Error('导出失败，请重试。');
      const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download='评审报告.docx';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);return;
    }
    if(b.dataset.adminPage){if(b.dataset.adminPage==='projects'){adminUI.projectPage=Number(b.dataset.page);render();}else if(b.dataset.adminPage==='history'){adminUI.historyPage=Number(b.dataset.page);render();}else{if(b.dataset.adminPage==='timeline')adminUI.timelinePage=Number(b.dataset.page);else adminUI.recordPage=Number(b.dataset.page);await loadAdminView();}return;}
    if(b.dataset.adminReset){const f=b.dataset.adminReset==='projects'?adminUI.projectFilters:adminUI.filters;Object.keys(f).forEach(k=>f[k]='');adminUI.projectPage=1;adminUI.recordPage=1;return loadAdminView();}
    if(b.dataset.adminAssign){const p=state.projects.find(p=>p.id===b.dataset.adminAssign);modal('分配项目',`<h3>${esc(projectName(p))}</h3><label class="form-field">所属用户<select name="username">${options({'':'历史项目（暂不分配）',...Object.fromEntries(adminUI.accounts.filter(a=>a.role==='user'&&a.enabled).map(a=>[a.username,a.username]))},p.owner||'')}</select></label><input name="project_id" type="hidden" value="${esc(p.id)}">`,button('取消','type="button" data-close')+'<button class="primary" type="submit">保存分配</button>','admin-owner-form');return;}
    if(b.dataset.adminPassword){modal('重置密码',`<h3>${esc(b.dataset.adminPassword)}</h3><label class="form-field">新密码<input name="password" type="password" autocomplete="new-password" minlength="8" maxlength="200" required></label><input name="username" type="hidden" value="${esc(b.dataset.adminPassword)}">`,button('取消','type="button" data-close')+'<button class="primary" type="submit">保存新密码</button>','admin-password-form');return;}
    if(b.dataset.adminEnabled){await api(`/api/admin/accounts/${id(b.dataset.adminEnabled)}`,'PATCH',{enabled:b.dataset.enabled==='1'});await loadAdminView();toast('账号状态已更新。');}
  });
});
document.addEventListener('submit',event=>{
  const form=event.target;if(!form.id.startsWith('admin-'))return;event.preventDefault();
  guarded(async()=>{
    const values=Object.fromEntries(new FormData(form));
    if(form.id.endsWith('-filter')){
      if(values.after&&values.before&&values.after>values.before){toast('起始日期不能晚于结束日期。');return;}
      if(form.id==='admin-projects-filter'){Object.assign(adminUI.projectFilters,values);adminUI.projectPage=1;}else{Object.assign(adminUI.filters,values);adminUI.recordPage=1;}
      return loadAdminView();
    }
    if(form.id==='admin-owner-form')await api(`/api/admin/projects/${id(values.project_id)}/owner`,'PUT',{username:values.username||null});
    if(form.id==='admin-password-form')await api(`/api/admin/accounts/${id(values.username)}`,'PATCH',{password:values.password});
    $('#editor').close();await loadAdminView();toast('已保存。');
  });
});
document.addEventListener('DOMContentLoaded',()=>$('#editor').addEventListener('close',()=>{if(!$('#editor').open)$('#editor').classList.remove('admin-drawer');}));

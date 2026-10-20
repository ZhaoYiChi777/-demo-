'use strict';
const USER_MODE = document.body.dataset.workspace === 'user';
const API = window.REVIEW_API_BASE || `${location.protocol === 'https:' ? 'https:' : 'http:'}//${location.hostname || '127.0.0.1'}:8000`;
const CAT = { common: '通用标准', special: '专项标准', topic: '课题要求' };
const RESULT = { pass: '符合', fail: '不符合', pending: '待确认', not_applicable: '不适用' };
const NAV = USER_MODE ? { overview:'我的任务', chat:'评审对话', projects:'项目材料', review:'问题与复核', reports:'报告', comparison:'报告对比', batch:'项目组', help:'使用指南' } : { overview:'管理概览', projects:'项目管理', reports:'工作记录', rules:'评审规则', models:'模型配置', accounts:'账号管理', help:'使用指南' };
const SOURCE_CATS = { project_report: '项目申报材料', drawing: '图纸', review_opinion: '历史评审意见', standard: '标准规范', template: '模板', other: '其他附件' };
const STEPS = [['material','材料识别'],['common','通用标准'],['special','专项标准'],['topic','课题要求'],['decision','综合结论'],['report','评审报告']];
const state = { view: 'overview', projects: [], project: null, materials: null, packages: [], rules: [], run: null, step: 'decision', mode: 'list', category: 'all', result: 'issues', selected: null, chunk: null, busy: false, scope: null, rulesCategory: 'all', rulesSearch: '', knowledge: null };
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const id = value => encodeURIComponent(value);
const date = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
const specialty = value => ({ TURB: '汽机', BOIL: '锅炉', ELEC: '电气', turbine: '汽机', boiler: '锅炉', electrical: '电气', unknown: '尚未确定' }[value] || value || '尚未确定');
const projectName = project => ({ 'Phase34 regression': '材料解析与归档验证项目' }[project?.name] || (/^[?？\s]*$/.test(project?.name || '') ? '未命名项目' : project.name));
const projectType = value => ({ technical_change: '技术改造', new_system: '新建系统', safety_reliability: '安全可靠性改造' }[value] || (/^[?？\s]*$/.test(value || '') ? '尚未填写' : value));
const projectReference = project => `<details class="project-reference"><summary>查看项目编号</summary><small>项目编号：${esc(project.id)}</small></details>`;
const badge = (result, label) => `<span class="pill ${esc(result || 'pending')}">${esc(label || RESULT[result] || '未开始')}</span>`;
const ruleReference = rule => `<details class="rule-reference"><summary>查看规则编号与版本</summary><small>规则编号：${esc(rule.id)} · 第 ${rule.version} 版</small></details><small class="print-only">规则编号：${esc(rule.id)} · 第 ${rule.version} 版</small>`;
const button = (label, action, cls = 'secondary') => `<button class="${cls}" ${action}>${label}</button>`;
const options = (items, selected) => Object.entries(items).map(([key, name]) => `<option value="${esc(key)}"${String(selected) === key ? ' selected' : ''}>${esc(name)}</option>`).join('');
const panel = (title, subtitle, content, action = '') => `<section class="panel"><div class="panel-head"><div><h2>${title}</h2>${subtitle&&!USER_MODE ? `<p>${subtitle}</p>` : ''}</div>${action}</div>${content}</section>`;
const heading = (title, subtitle, actions = '', eyebrow = '项目评审') => `<section class="page-heading"><div>${USER_MODE?'':`<div class="eyebrow">${eyebrow}</div>`}<h1>${esc(title)}</h1>${subtitle&&!USER_MODE?`<p>${esc(subtitle)}</p>`:''}</div><div class="heading-actions">${actions}</div></section>`;
let toastTimer;
let projectEpoch = 0;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 4200); }
async function api(path, method = 'GET', body) {
  const response = await fetch(API + path, { method, ...(body instanceof FormData ? { body } : body !== undefined ? { body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } } : {}) });
  if(response.status===401)throw new Error('登录已失效，请重新登录。');
  if (!response.ok) { let message; try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : data.detail?.map(x => x.msg).join('；'); } catch (_) {} throw new Error(message || `请求未完成（${response.status}），请重试。`); }
  return response.json();
}
async function guarded(action) { try { return await action(); } catch (error) { toast(error.message === 'Failed to fetch' ? '无法连接评审服务，请检查本机服务后重试。' : error.message); return null; } }
function navigate(view) {
  const route=USER_MODE?applyUserRoute(view):applyAdminRoute(view);
  if(USER_MODE&&route.view==='chat'&&state.view==='review'&&state.run){userUI.chatRestored=true;localStorage.setItem('review-workbench-selected-report',state.run.id);}
  state.view=route.view;
  if(USER_MODE&&location.hash!==`#${route.path}`)history.pushState(null,'',`#${route.path}`);
  else history.replaceState(null,'',`#${route.path}`);
  render();window.scrollTo({top:0});
  if(!USER_MODE&&['overview','projects','reports','accounts'].includes(state.view))return guarded(loadAdminView);
  if(state.view==='reports')return guarded(loadPassedReports);
  if(state.view==='models')return guarded(loadModelSettings);
  if(state.view==='batch')return guarded(USER_MODE&&userUI.projectId?loadUserProjectHistory:loadProjectBatch);
  if(USER_MODE&&state.view==='overview')return guarded(loadUserTasks);
  if(USER_MODE&&state.view==='chat')return guarded(loadUserReportChoices);
}
function noProject() { return `<section class="panel empty"><h2>先选择一份待评审材料</h2>${button('选择项目', 'data-view="batch"', 'primary')}</section>`; }
function runTitle(run) { if(run.project_conclusion) return run.project_conclusion.label + (run.project_conclusion.provisional ? '（初步意见）' : ''); return run.result === 'fail' ? '不符合评审要求' : run.result === 'pass' ? '符合评审要求' : '存在待确认事项，暂无法判断'; }
function conclusionStyle(run) { const c=run.project_conclusion; return !c ? run.result : c.level===3 ? 'fail' : c.level===0 && !c.provisional ? 'pass' : 'pending'; }
function topicTitle(run) { return run.topic_result === 'fail' ? '不符合课题要求' : run.topic_result === 'pass' ? '符合课题要求' : !run.brief?.trim() ? '课题要求：尚未提供，待核验' : '课题要求：待确认'; }
function reviewProgressTitle(run) {
  if(run.project_conclusion) return runTitle(run);
  return run.result === 'pass' ? '本次检查符合评审要求' : run.result === 'fail' ? '已形成评审意见，需完善相关内容' : '已形成阶段性评审意见，待补充核验';
}
function ruleContractDetails(r) {
  const m=r.source_metadata;
  return m ? `<p class="muted">原规则范围：${esc(m.project_types)} / ${esc(m.system_scope)}<br>本条适用条件：${esc(r.applicability_condition||'见原规则')}<br>原问题级别：${esc(m.problem_level)} · ${esc(m.hardness)}<br>结论影响：${esc(m.conclusion_impact)}</p><ol>${(r.criteria||[]).map(c=>`<li>${esc(c)}</li>`).join('')}</ol>` : '';
}
function pendingExplanation(run) {
  const pending = (run.checks || []).filter(c => c.result === 'pending');
  if (!pending.length) return '';
  const groups = { '待提供课题要求': 0, '待重新执行判定': 0, '待明确规则适用范围': 0, '待定位原文证据': 0, '待补充材料或专业复核': 0 };
  for (const c of pending) {
    const j = c.judgment || {};
    const name = c.category === 'topic' && !run.brief?.trim() ? '待提供课题要求'
      : !c.reviewed_by && (['rejected', 'unavailable', 'disabled'].includes(j.status) || (j.criteria||[]).some(k=>['processing_error','retrieval_incomplete'].includes(k.pending_reason))) ? '待重新执行判定'
      : !c.reviewed_by && j.applicability === 'uncertain' ? '待明确规则适用范围'
      : !c.evidence?.length ? '待定位原文证据' : '待补充材料或专业复核';
    groups[name]++;
  }
  return `<details class="review-pending-explanation"><summary>待确认 ${pending.length} 项：查看后续核验安排</summary><p class="summary-text">待确认表示当前证据或核验条件尚不足，不等同于已判定不符合。请结合原文确认适用范围，再补充材料或进行人工复核。</p><ul>${Object.entries(groups).filter(([,n])=>n).map(([name,n])=>`<li>${name}：${n} 项</li>`).join('')}</ul></details>`;
}
function isStale() {
  if (!state.run || !state.materials) return false;
  const active = state.rules.filter(r => r.enabled);
  const snapshot = state.run.checks.filter(c => c.rule.origin !== 'rubric');
  const sources = state.materials.sources.filter(s => !state.run.material_package_id || s.material_package_id === state.run.material_package_id);
  return active.length !== snapshot.length || active.some(r => !snapshot.some(c => c.id === r.id && c.rule.version === r.version)) || state.materials.brief !== state.run.brief ||
    JSON.stringify(sources.map(s => [s.id,s.document_category,s.parse_status])) !== JSON.stringify(state.run.sources.map(s => [s.id,s.document_category,s.parse_status]));
}
function staleNotice() { return isStale() ? `<div class="notice" style="margin-bottom:16px">材料或规则已更新。当前显示 ${esc(state.run.id)} 的评审快照，重新评审后更新结论。 ${button('按当前规则重新评审', 'data-run', 'subtle')}</div>` : ''; }
function render() {
  $('#breadcrumb').textContent = USER_MODE?userBreadcrumb():NAV[state.view];
  const activeView=USER_MODE ? ({review:'overview',projects:'batch',comparison:'batch'}[state.view]||state.view) : state.view;
  document.querySelectorAll('.sidebar [data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === activeView));
  const views = { overview: renderOverview, projects: renderProjects, review: renderReview, rules: renderRules, reports: renderReports, comparison: renderComparison, batch: renderProjectBatch, models: renderModelSettings, help: renderHelp };
  if(USER_MODE)Object.assign(views,{overview:renderUserTasks,chat:renderUserChat,review:renderUserReview,batch:renderUserGroups,projects:renderUserMaterials,reports:renderUserReports,help:renderUserHelp});
  else Object.assign(views,{overview:renderAdminOverview,projects:renderAdminProjects,reports:renderAdminRecords,accounts:renderAdminAccounts,help:renderAdminHelp});
  $('#app').innerHTML = views[state.view]();
  if(USER_MODE)stripUserHints($('#app'));
  if(state.project&&((USER_MODE&&state.view==='chat'&&state.run)||(!USER_MODE&&state.view==='review')))mountReviewAgent();
}
function projectTable(projects) {
  return `<div class="table-scroll"><table><thead><tr><th>项目名称</th><th>专业 / 类型</th><th>当前记录</th><th>操作</th></tr></thead><tbody>${projects.map(p => `<tr><td><strong>${esc(projectName(p))}</strong>${projectReference(p)}${p.id === 'DEMO-REVIEW' ? '<small>演示样例</small>' : ''}</td><td>${esc(specialty(p.specialty))}<small>${esc(projectType(p.project_type))}</small></td><td>${state.run?.project_id === p.id ? badge(conclusionStyle(state.run),runTitle(state.run)) : `<span class="muted">${esc(({ uploaded:'材料已提交', pending_expert:'已有历史评审', expert_confirmed:'历史专家已确认', archived:'历史已归档' })[p.status] || '待打开查看')}</span>`}</td><td>${button('进入评审 →', `data-project="${esc(p.id)}"`, 'subtle')}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">暂无项目，创建一个项目开始评审。</td></tr>'}</tbody></table></div>`;
}
function renderOverview() {
  const run = state.run;
  return heading('导航', '从材料到结论，让每一项评审可查看、可核验、可追溯。', button('＋ 新建评审项目', 'data-new-project', 'primary'), '项目评审 / 工作空间') +
    `<div class="intro-banner"><div><h2>依据评审标准，逐项核验项目材料。</h2><p>按照通用标准、专项标准及课题要求开展评审，记录核验依据，形成评审结论与修改建议。</p></div>${button(state.project ? '继续当前评审 →' : '体验完整评审 →', state.project ? 'data-view="review"' : 'data-demo', 'primary')}</div>` +
    `<section class="stat-grid">${[['projects','项目总数',state.projects.length,'当前项目库','查看项目列表'],['all','当前检查项',run?.counts.total ?? '—',run ? '当前项目所选评审记录' : '尚未生成评审','查看全部检查项'],['fail','不符合项',run?.counts.fail ?? '—','需要修改的检查项','查看不符合项'],['pending','待确认项',run?.counts.pending ?? '—','需要补充证据或人工核验','查看待确认项']].map(([key,label,value,note,action])=>`<button type="button" class="stat-card" data-stat="${key}" aria-label="${action}"><span>${label}</span><strong>${value}</strong><small>${note}</small><span class="stat-link">${key!=='projects'&&!run?'查看评审准备':action} →</span></button>`).join('')}</section>` +
    reviewEntrances() + (run ? summary(run) : '') + panel('近期项目', '点击项目即可查看材料、评审依据与结论', projectTable(state.projects.slice(0,6)), button('全部项目 →','data-view="projects"','subtle')) +
    `<div class="section-title"><h2>评审主线</h2><p>点击步骤，查看操作说明</p></div><section class="panel flow-overview">${STEPS.map(([key,name],i)=>`<button type="button" data-step-help="${key}" aria-haspopup="dialog" aria-label="${name}：查看操作说明"><b>0${i+1}</b><strong>${name}</strong><small>${['识别章节与资料','检查材料规范','核验专业要求','对照任务目标','解释判断原因','查看与导出'][i]}</small><span class="flow-help-link">查看说明 ↗</span></button>`).join('')}</section>`;
}
function renderProjects() {
  return heading(USER_MODE?'项目材料':'项目与材料', '项目档案、课题任务要求和原始材料统一管理。', USER_MODE?button('返回项目组','data-view="batch"')+button('＋ 新建评审','data-new-project','primary'):button('＋ 新建评审项目','data-new-project','primary')) +
    panel('全部项目',`${state.projects.length} 个项目`,projectTable(state.projects)) +
    (state.project ? `<div class="section-title"><div><h2>当前项目：${esc(projectName(state.project))}</h2><p>材料更新后，重新评审以获取新结论。</p></div>${button('进入评审 →','data-view="review"','subtle')}</div>${materialsContent()}` : '');
}
function summary(run) {
  const next = run.result === 'pass' ? '可查看本次评审报告及核验依据。' : run.result === 'fail' ? '建议优先复核不符合项，按意见完善材料，再完成待确认事项的核验。' : '建议结合原文补充依据，完成待确认事项的核验后形成最终意见。';
  return retrievalNote(run.retrieval)+judgmentNote(run.judgment)+`<section class="summary" aria-label="综合评审摘要"><div class="summary-top"><span class="summary-symbol ${conclusionStyle(run)}">${conclusionStyle(run) === 'pass' ? '✓' : conclusionStyle(run) === 'fail' ? '!' : '◷'}</span><div><span class="summary-caption">综合评审意见${run.is_demo ? ' · 演示样例' : ''}</span><div class="summary-title"><h2>${reviewProgressTitle(run)}</h2></div><div class="row wrap">${badge(run.result,'逐项符合性：'+RESULT[run.result])}${badge(run.topic_result,topicTitle(run))}</div><p class="summary-text">本次纳入 ${run.counts.total} 项规则检查，已确认符合 ${run.counts.pass} 项、不符合 ${run.counts.fail} 项，待确认 ${run.counts.pending} 项${run.counts.not_applicable ? `，另有 ${run.counts.not_applicable} 项不适用` : ''}。${esc(run.project_conclusion?.explanation || next)}</p><p class="summary-text">检查项数量按规则统计，同一事项可能关联多条规则，不代表独立问题数量。</p>${pendingExplanation(run)}<p class="summary-text">本次结论范围：${esc(run.scope_name || (run.material_package_id ? '资料包 '+run.material_package_id : '整个项目'))}</p></div><div class="summary-actions">${button('查看评审报告 ↗','data-current-report')}</div></div><div class="summary-footer"><span><b>${run.counts.total}</b>检查规则</span><span class="pass-text"><b>${run.counts.pass}</b>符合</span><span class="fail-text"><b>${run.counts.fail}</b>不符合</span><span class="pending-text"><b>${run.counts.pending}</b>待确认</span><span style="margin-left:auto">${esc(date(run.created_at))}</span></div></section>`;
}
function categories(run) { return `<section class="category-grid">${run.categories.map(c=>`<button class="category-card ${state.step === c.id ? 'active' : ''}" data-step="${c.id}"><div class="row spread"><strong>${c.name}</strong>${badge(c.result)}</div><div class="row spread"><p>${c.total} 项检查 · ${c.issues} 项待处理（含待确认）${c.total === 0 ? ' · 未配置启用规则' : ''}</p><span class="arrow">→</span></div></button>`).join('')}</section>`; }
function rail() {
  return `<aside class="review-rail" aria-label="评审主线"><div class="rail-label">评审主线 / 6 个步骤</div>${STEPS.map(([key,name],i)=>{
    const cat = state.run?.categories.find(c=>c.id===key);
    let status = state.run ? cat?.issues ? '存在问题' : '已完成' : '未开始';
    if (key === 'material') status = state.materials?.chunks.length ? '已完成' : '未开始';
    if (state.busy && ['common','special','topic','decision'].includes(key)) status = '评审中';
    return `<button class="rail-step ${state.step===key?'active':''}" data-step="${key}" aria-current="${state.step===key?'step':'false'}"><span>${i+1}</span><strong>${name}</strong><small class="${status==='存在问题'?'rail-issue':''}">${status}</small></button>`;
  }).join('')}</aside>`;
}
function renderReview() {
  if (!state.project) return noProject();
  const scopePicker = `<label class="scope-picker">下次评审范围<select id="review-scope" aria-label="下次评审范围" ${state.busy?'disabled':''}>${options({'':'整个项目',...Object.fromEntries(state.packages.map(p=>[p.id,p.name]))},state.scope??'')}</select></label>`;
  const actions = button('＋ 上传报告','data-upload') + scopePicker + button(state.busy?'评审中…':state.run?'重新评审 ↻':'开始评审 →',`data-run ${state.busy?'disabled':''}`,'primary');
  let content;
  if (state.step === 'material') content = materialsContent();
  else if (state.step === 'report') content = reportContent();
  else if (!state.run) content = panel('准备开始评审','先确认材料和评审规则，再执行逐项检查。',`<div class="empty"><h2>当前项目尚未生成评审</h2><p>已加载 ${state.materials?.sources.length || 0} 份材料，${state.rules.filter(r=>r.enabled).length} 条启用规则。</p>${button('查看材料','data-step="material"')} ${button('查看评审规则','data-view="rules"')}</div>`);
  else content = checksContent();
  return heading(projectName(state.project),`${state.run?.material_package_id?'当前报告专业':'项目专业'}：${specialty((state.run?.project||state.project).specialty)} · 项目类型：${projectType(state.project.project_type)}${state.project.id==='DEMO-REVIEW'?' · 示例材料，仅用于功能体验':''}`,actions,'项目评审 / 评审辅助对话')+projectReference(state.project)+
    staleNotice()+(state.run?summary(state.run)+categories(state.run):'')+`<div class="workbench-layout">${rail()}<div class="review-content gap">${content}</div></div>`;
}
function visibleChecks() { return (state.run?.checks || []).filter(c=>(state.category==='all'||c.category===state.category)&&(state.result==='all'||state.result==='issues'&&['fail','pending'].includes(c.result)||c.result===state.result)); }
function checksContent() {
  const all = state.run.checks;
  const list = visibleChecks();
  const selected = list.find(c=>c.id===state.selected);
  const name = CAT[state.step] || '问题定位与综合结论';
  return panel(name,CAT[state.step] ? `${{common:'材料本身是否符合通用规范？',special:'是否满足电力业务的专业要求？',topic:'是否覆盖本次任务目标和验收要求？'}[state.step]}` : '从问题列表出发，查看依据并定位原文。',
    `<div class="toolbar"><div class="segmented" aria-label="评审视图">${(USER_MODE?[['list','问题列表'],['document','原文与批注']]:[['list','问题列表'],['document','文档批注'],['process','评审过程']]).map(([key,label])=>button(label,`data-mode="${key}"`,state.mode===key?'active':'')).join('')}</div>${USER_MODE?'':button('管理评审规则 ↗','data-view="rules"','subtle')}</div>`+
    `<div class="filters review-filters"><select id="category-filter" aria-label="评审类别">${options({all:'全部类别',...CAT},state.category)}</select><select id="result-filter" aria-label="评审结果">${options({issues:'全部问题',all:'全部检查项',fail:'不符合',pending:'待确认',pass:'符合'},state.result)}</select><span class="filter-count">显示 ${list.length} / ${all.length} 项</span></div>`+
    (state.mode==='document'?documentContent(selected || list[0]):state.mode==='process'?processContent():
      list.length?list.map((c,i)=>`<div><button class="issue-row ${c.id===state.selected?'selected':''}" data-check="${esc(c.id)}" aria-expanded="${c.id===state.selected}"><span class="issue-number">${String(i+1).padStart(2,'0')}</span><div class="issue-copy"><strong>${esc(c.rule.name)}</strong><small>${CAT[c.category]} · ${c.reviewed_by?'专家已复核':c.method}</small></div>${badge(c.result)}<span class="arrow">${c.id===state.selected?'⌄':'›'}</span></button>${c.id===state.selected?checkDetail(c):''}</div>`).join(''):
        `<div class="empty">当前筛选下没有检查项。${button('查看全部检查项','data-reset-filters','subtle')}</div>`));
}
function retrievalNote(r){
  if(!r)return '';
  const labels={complete:'字段匹配与大模型语义检索已完成',partial:'语义检索部分完成，其余部分仅保留字段结果',unavailable:'语义检索未完成，本次仅保留字段结果',disabled:'本次仅使用字段匹配，语义检索未启用',not_needed:'当前没有需要检索的规则或原文'};
  if(r.coverage_policy==='per-rule-window-v1'){labels.partial='部分检索窗口未完成，已核验证据保留；仅相关判据需补充核验';labels.unavailable='本轮语义检索未完成，请查看各判据的补检索及核验结果';}
  return `<div class="notice retrieval-note retrieval-${esc(r.status)}"><strong>${labels[r.status]||'检索状态待确认'}</strong>${r.total_batches?`<p>语义检索完成 ${r.completed_batches} / ${r.total_batches} 组</p>`:''}${r.field_matches!==undefined?`<p>字段证据 ${r.field_matches} 段 · 语义证据 ${r.semantic_matches} 段（可重合）</p>`:''}${r.rejected_evidence?`<p>${r.rejected_evidence} 条无法核验的模型引用已排除。</p>`:''}</div>`;
}
function judgmentNote(j){
  if(!j)return '';
  const labels={complete:'语义判定已完成',partial:'语义判定部分完成，未完成或引用无效的检查保留待确认',unavailable:'语义判定未完成，需要人工核验',disabled:'语义判定未启用，需要人工核验',not_needed:'本次无可执行的语义判定项'};
  return `<div class="notice judgment-note"><strong>${labels[j.status]||'语义判定状态待确认'}</strong><p>已完成 ${j.evaluated_checks||0} 项判定${j.total_batches?` · 请求 ${j.completed_batches} / ${j.total_batches} 组`:''}${j.rejected_checks?` · ${j.rejected_checks} 项未通过结果核验`:''}${j.partial_checks?` · ${j.partial_checks} 项部分完成（保留有效子项）`:''}。证据不足的检查仍为待确认；自动结论可人工复核。</p></div>`;
}
function judgmentDetail(c){
  const j=c.judgment||{};if(!j.criteria?.length&&!c.retrieval?.failed_windows?.length&&!j.supplemental_search)return '';
  const states={before:'改造前',after:'改造后',scope:'本次范围',unspecified:'原文未明确'};
  const supplement=j.supplemental_search;
  return `<details class="judgment-detail"><summary>查看自动判据与判定引用${c.reviewed_by?'（人工复核前）':''}</summary>${supplement?`<p>已针对 ${supplement.criteria.length} 条判据补检索，完成 ${supplement.retrieval.completed_batches}/${supplement.retrieval.total_batches} 组。</p>`:''}${(j.criteria||[]).map(k=>`<div class="detail-pair"><strong>${esc(k.criterion)} · ${RESULT[k.result]}</strong><p>${esc(k.reason)}</p>${k.arithmetic?`<p class="notice">${esc(k.arithmetic.reason)}</p>`:''}${(k.evidence||[]).map(e=>`<button class="evidence-link" data-evidence="${e.paragraph_id}" data-for-check="${esc(c.id)}">片段 ${e.paragraph_id} ↗ ${esc(e.quote)}${e.object?`<br>对象：${esc(e.object)} · ${esc(states[e.state]||'原文未明确')} ${esc(String(e.value??'')+String(e.unit??''))}`:''}</button>`).join('')}</div>`).join('')}${(c.retrieval?.failed_windows||[]).map(w=>`<p class="muted">未完成窗口：段落 ${w.paragraph_id}，字符 ${w.start}—${w.end}；${esc(w.error)}</p>`).join('')}</details>`;
}
function evidenceLabel(e){return (e.retrieval_sources||[]).map(s=>({semantic:'语义命中',field:'字段命中',judgment:'判定补充引用',context:'相邻段落'}[s]||s)).join(' + ');}
function checkDetail(c) {
  return `<div class="check-detail">${retrievalNote(c.retrieval)}${judgmentDetail(c)}<div class="detail-pair"><span class="detail-label">评审标准</span><p>${esc(c.rule.standard)}</p><button class="rule-link" data-rule-snapshot="${esc(c.id)}">查看“${esc(c.rule.name)}”的评审依据</button></div><div class="detail-pair"><span class="detail-label">材料实际情况 / 原文证据</span>${c.evidence.length?c.evidence.map(e=>`<button class="evidence-link" data-evidence="${e.id}" data-for-check="${esc(c.id)}">${esc(e.filename)} · ${esc(e.section_path)}${e.page?` · 第 ${e.page} 页`:''} ↗<br>${esc(evidenceLabel(e))}<br>${esc(e.semantic_quotes?.[0]?.quote||e.text.slice(0,180))}${!e.semantic_quotes?.length&&e.text.length>180?'…':''}</button>`).join(''):`<p>未定位到匹配原句。${c.missing.length?`已解析材料中未检出：${esc(c.missing.join('、'))}。`:''}可打开材料全文核对。</p>`}</div><div class="detail-grid"><div class="detail-callout fail-reason"><span class="detail-label">${c.result==='fail'?'不符合原因':'判断说明'}</span><p>${esc(c.reason)}</p></div><div class="detail-callout"><span class="detail-label">修改建议</span><p>${esc(c.suggestion)}</p></div></div><div class="detail-actions">${button('定位原文 ↗',`data-locate="${esc(c.id)}"`)}${USER_MODE?'':button('编辑对应规则',`data-edit-rule="${esc(c.id)}"`)}${state.run.status==='archived'?'<span class="muted">已归档</span>':button('复核本项',`data-adjudicate="${esc(c.id)}"`,'primary')}</div>${c.reviewed_by?`<p class="muted" style="font-size:10px;margin-top:12px">${c.reviewed_by} · ${esc(date(c.reviewed_at))}</p>`:''}</div>`;
}
function documentContent(check) {
  const run = state.run;
  const chunks = run.chunks;
  const chosen = check?.evidence.some(e=>e.id===state.chunk) ? state.chunk : check?.evidence[0]?.id;
  return `<div class="doc-layout">${check&&!check.evidence.length?`<div class="notice doc-warning">此项未匹配到原句；缺失内容无法标注在不存在的位置。以下为本次评审材料全文，右侧保留当前批注。</div>`:''}<article class="document" aria-label="原始材料文本"><small>原始材料 · 解析文本视图</small><h3>${esc(projectName(run.project))}</h3>${chunks.map(c=>{const linked=run.checks.filter(x=>x.evidence.some(e=>e.id===c.id));return `<section class="doc-chunk ${c.id===chosen?'selected':''}" id="chunk-${c.id}"><small>${esc(c.filename)} · 片段 ${c.id}${c.page?` · 第 ${c.page} 页`:''}</small><h4>${esc(c.section_path)}</h4><p>${esc(c.text)}</p><div class="chunk-actions">${linked.map(x=>button(`${RESULT[x.result]} · ${esc(x.rule.name)}`,`data-annotation="${esc(x.id)}" data-chunk="${c.id}"`)).join('')}</div></section>`;}).join('')||'<p class="empty">暂无可显示原文。</p>'}</article><aside class="annotation" aria-label="对应评审批注">${check?`<div class="annotation-header"><div class="row spread"><small class="muted">${CAT[check.category]} / 评审批注</small>${badge(check.result)}</div><h3>${esc(check.rule.name)}</h3></div>${checkDetail(check)}`:'<div class="empty">点击原文旁的批注打开检查结果。</div>'}</aside></div>`;
}
function processContent() {
  const run=state.run, items=run.checks.filter(c=>state.category==='all'||c.category===state.category);
  const missing=items.filter(c=>c.result==='fail'), pending=items.filter(c=>c.result==='pending');
  const steps=[['识别材料关键内容',`本次纳入 ${run.sources.length} 份来源材料，抽取 ${run.chunks.length} 个原文片段。章节：${[...new Set(run.chunks.map(c=>c.section_path))].slice(0,12).join('、')}。`],['匹配评审规则',`本次检查依据共 ${items.length} 项，点击下方规则名称可查看具体标准。${items.length ? '' : '当前范围没有启用规则。'}`],['执行逐项检查',`${items.filter(c=>c.rule.mode==='presence').length} 项字段存在性检查；${items.filter(c=>c.rule.mode==='manual').length} 项按判据语义评审。字段未命中时核验等价表述；结论需有原文依据，证据不足保留待确认。`],['发现问题',`发现 ${missing.length} 项不符合、${pending.length} 项待确认。${[...missing,...pending].slice(0,8).map(c=>c.rule.name).join('；')}。`],['形成结论',`不符合项优先决定类别不符合；无不符合但存在待确认或未配置检查项时，类别为待确认；全部检查项符合时才判为符合。当前逐项符合性：${RESULT[run.result]}。${run.project_conclusion ? esc(run.project_conclusion.method) : ""}`]];
  return `<div class="process">${steps.map(([name,text],i)=>`<details><summary><span>0${i+1}</span>${name}</summary><p>${esc(text)}</p>${i===1&&items.length?`<ul class="process-rule-list">${items.map(c=>`<li><button class="rule-link" data-rule-snapshot="${esc(c.id)}">${esc(c.rule.name)}</button></li>`).join('')}</ul>`:''}</details>`).join('')}</div>`;
}
function materialsContent() {
  const m=state.materials;
  if (!m) return '<div class="empty">材料尚未加载。</div>';
  const sourceList=m.sources.map(s=>{
    const report=state.packages.find(p=>p.id===s.material_package_id);
    return `<div class="material-item"><span class="file-mark">${esc(s.file_kind.toUpperCase())}</span><div class="file-copy"><strong>${esc(s.filename)}</strong><p>${esc(s.source_path)} · ${esc(({parsed:'已解析',ocr_required:'待文字识别',conversion_failed:'转换未完成',empty_content:'未提取到文字',container:'压缩包',unsupported:'格式暂不支持'})[s.parse_status] || s.parse_status)}</p>${report?`<p>所属报告：${esc(report.name)}</p>`:''}${visualSourceSummary(s)}</div><select data-source-category="${s.id}" aria-label="${esc(s.filename)}资料分类">${options(SOURCE_CATS,s.document_category)}</select>${button('保存',`data-save-source="${s.id}"`)}</div>`;
  }).join('');
  const reports=state.packages.map(p=>`<div class="package-row"><p>${esc(p.name)} · ${p.document_count || 0} 份材料</p><div class="row wrap">${button('补充附件',`data-add-attachment="${p.id}"`)}${button('评审此报告',`data-run-package="${p.id}"`)}</div></div>`).join('');
  return `<div class="gap">${panel('材料识别',`${m.sources.length} 份来源材料 · ${m.sources.filter(s=>s.parse_status==='parsed').length} 份已解析 · ${m.chunks.length} 个可评审原文片段`,sourceList||'<div class="empty">上传项目申报书后开始评审。</div>',button('＋ 上传报告','data-upload','subtle'))}${panel('本次课题 / 任务要求','单独录入任务目标、必须交付的成果和验收指标，作为课题符合性的比较基准。',`<form id="brief-form" class="panel-body brief-box"><label class="form-field">课题任务要求<textarea name="text" rows="5" placeholder="例如：本次改造需达到的可靠性目标值、交付成果、验收方法和完成时限。">${esc(m.brief)}</textarea></label>${briefUpload('text')}<div class="row spread"><small class="muted">保存后需重新评审，历史结论保留原要求。</small><button class="primary" type="submit">保存课题要求</button></div></form>`)}${panel('报告与附件','',reports||'<div class="empty">上传报告后自动分组</div>')}</div>`;
}
function attachmentForm(reportId){
  const selected=reportId??(state.packages.length===1?state.packages[0].id:'');
  modal('补充附件',`<label class="form-field">所属报告<select name="material_package_id" required>${options({'':'请选择报告',...Object.fromEntries(state.packages.map(p=>[p.id,p.name]))},selected)}</select></label><label class="form-field">附件文件<input name="attachment" type="file" accept="${esc($('#file-upload').accept)}" required></label>`,button('取消','type="button" data-close')+'<button class="primary" type="submit">上传附件</button>','attachment-form');
}
async function uploadProjectFile(file,reportId=null){
  const projectId=state.project.id,data=new FormData();data.append('file',file);
  if(reportId!==null)data.append('material_package_id',String(reportId));
  else data.append('document_type','project_report');
  toast(`正在上传并解析 ${file.name}…`);
  await api(`/api/projects/${id(projectId)}/documents`,'POST',data);
  if(state.project?.id!==projectId)return;
  await refreshMaterial();state.project=state.materials.project;
  state.rules=(await api(`/api/workbench/rules?project_id=${id(projectId)}`)).rules;state.step='material';render();
}
function renderRules() {
  const rules=state.rules.filter(r=>(state.rulesCategory==='all'||r.category===state.rulesCategory)&&`${r.name} ${r.id} ${r.standard} ${r.content}`.toLowerCase().includes(state.rulesSearch.toLowerCase()));
  return heading('评审规则','',`<select id="admin-rule-project" aria-label="规则适用项目">${options({'':'全局评审规则',...Object.fromEntries(state.projects.map(p=>[p.id,projectName(p)]))},state.project?.id||'')}</select>`+button('＋ 新建自定义规则','data-new-rule','primary'),'评审依据 / 规则管理')+
    `<div class="notice" style="margin-bottom:18px">规则按通用、专项、课题要求分类。字段未命中时继续核验等价表述；专业检查按规则判据及原文形成结论，证据不足保留待确认，支持人工复核。编辑后重新评审，才会更新结论。</div>`+
    `<div class="toolbar panel" style="margin-bottom:18px"><div class="filters"><select id="rule-category" aria-label="规则类别">${options({all:'全部类别',...CAT},state.rulesCategory)}</select><input id="rule-search" type="search" placeholder="搜索规则名称、编号或标准" aria-label="搜索规则" value="${esc(state.rulesSearch)}"></div><small class="muted">${rules.length} 条规则 · ${state.rules.filter(r=>r.enabled).length} 条启用</small></div>`+
    `<div class="rule-grid">${rules.map(r=>`<article class="rule-card ${r.enabled?'':'disabled-rule'}"><div class="row spread"><span class="rule-id">${CAT[r.category]}</span><span class="pill">${r.origin==='default'?'系统默认':'用户自定义'}</span></div><h3>${esc(r.name)}</h3><small>${r.mode==='presence'?'字段与等价表述核验':'按判据语义评审'} · ${r.enabled?'已启用':'已停用'}</small><p>${esc(r.standard)}</p>${ruleReference(r)}<div class="row spread"><small>更新于 ${esc(date(r.updated_at))}</small>${button('查看 / 编辑',`data-edit-rule="${esc(r.id)}"`,'subtle')}</div></article>`).join('')||'<div class="panel empty">没有匹配的规则。</div>'}</div>`+
    `<div class="section-title"><h2>历史案例与专项知识</h2>${button('查看原有知识库','data-knowledge','subtle')}</div>${state.knowledge?panel('参考案例','案例用于参考，不计入当前材料检查数量。',`<div class="panel-body">${state.knowledge.cases.map(c=>`<details class="detail-pair"><summary>${esc(c.project_name)}</summary><p class="muted">${esc(c.basis)}<br>${esc(c.required)}<br>${esc(c.historical_opinion)}</p></details>`).join('')}<details><summary>专项技术参考（${state.knowledge.technical_rules.length} 条）</summary>${state.knowledge.technical_rules.map(r=>`<p>${esc(r.topic)}：${esc(r.check_points)}；${esc(r.acceptance)}</p>`).join('')}</details></div>`):''}`;
}
function qualificationAction(r) {
  if(r.qualification)return `<div class="notice" style="margin-bottom:16px"><strong>合格报告 · 人工确认</strong><p>确认时间：${esc(date(r.qualification.confirmed_at))}。本次报告已保留到历史记录；原始检查结果和评分保留。</p></div>`;
  if(state.view==='reports')return '';
  return `<div class="report-retention panel panel-body row spread wrap" style="margin-bottom:16px"><div><strong>保留当前报告</strong><p class="muted">人工确认为合格报告并加入历史记录，保留本次材料、检查结果及评分。</p></div>${button('确认为合格并加入历史记录',`data-qualify-run="${esc(r.id)}"`,'primary')}</div>`;
}
function reportContent(r=state.run) {
  if (!r) return `<section class="panel empty"><h2>尚未生成评审报告</h2><p>完成一次逐项评审后，可在线查看和导出报告。</p>${button('返回评审','data-view="review"','primary')}</section>`;
  const checks=r.checks, issues=checks.filter(c=>['fail','pending'].includes(c.result));
  return qualificationAction(r)+retrievalNote(r.retrieval)+judgmentNote(r.judgment)+`<div class="report-toolbar row spread wrap"><small class="muted">${esc(r.id)} · ${r.status==='archived'?'已归档':'当前评审快照'}${r.is_demo?' · 演示样例':''}</small><div class="row wrap">${button('导出 Word 文档','data-export','primary')}${button('打印 / 另存 PDF','data-print')}${r.status==='archived'||(!USER_MODE&&state.view==='reports')?'':button('归档本次评审','data-archive')}</div></div><article class="report-paper"><header><div class="eyebrow">项目评审报告${r.is_demo?' / 演示样例':''}</div><h1>${esc(projectName(r.project))}</h1><p>${esc(r.id)} · ${esc(date(r.created_at))}</p></header><section><h2>1. 评审基本信息</h2><p>项目名称：${esc(projectName(r.project))}\n项目编号：${esc(r.project_id)}\n评审时间：${esc(date(r.created_at))}\n材料：${r.sources.map(s=>esc(s.filename)).join('；')}\n评审范围：${r.material_package_id?'资料包 '+r.material_package_id:'整个项目'}${r.is_demo?'\n数据说明：示例材料，仅用于功能体验。':''}</p></section><section><h2>${r.qualification?'2. 原始自动评审结论':'2. 综合评审结论'}</h2><div class="row wrap">${badge(conclusionStyle(r),runTitle(r))}${badge(r.topic_result,topicTitle(r))}</div>${r.project_conclusion ? `<p>${esc(r.project_conclusion.explanation)}</p><p>逐项符合性：${RESULT[r.result]}</p>` : ""}</section><section><h2>3. 评审摘要</h2><p>${esc(r.summary)}</p><p class="muted">${esc(r.project_conclusion?.method || '不符合优先，待确认次之；全部检查项符合时才判为符合。无启用检查项的类别为待确认。')}</p></section><section><h2>4. 三类评审结果</h2><table><thead><tr><th>评审类别</th><th>结果</th><th>检查数</th><th>问题数（含待确认）</th></tr></thead><tbody>${r.categories.map(c=>`<tr><td>${c.name}</td><td>${RESULT[c.result]}</td><td>${c.total}</td><td>${c.issues}</td></tr>`).join('')}</tbody></table></section><section><h2>5. 主要问题</h2>${issues.map(c=>`<p>【${RESULT[c.result]}】${esc(c.rule.name)}：${esc(c.reason)}</p>`).join('')||'<p>本次检查未发现不符合或待确认项。</p>'}</section><section><h2>6. 评审依据</h2><p>本次课题要求：${esc(r.brief||'尚未提供')}</p>${checks.map(c=>`<p><strong>${esc(c.rule.name)}</strong> · ${c.rule.origin==='default'?'系统默认':'用户自定义'}\n标准：${esc(c.rule.standard)}\n规则内容：${esc(c.rule.content)}</p>${ruleReference(c.rule)}`).join('')}</section><section><h2>7. 详细评审意见</h2>${checks.map(c=>`<article><h3>${esc(c.rule.name)} · ${RESULT[c.result]}</h3><p>评审规则：${esc(c.rule.name)} · ${CAT[c.category]}\n执行方式：${c.method} · ${c.reviewed_by||'系统检查记录'}\n评审标准：${esc(c.rule.standard)}</p>${judgmentDetail(c)}${c.evidence.map(e=>`<blockquote>${esc(e.filename)} · ${esc(e.section_path)} · 片段 ${e.id}\n${esc(e.text)}</blockquote>`).join('')||'<p>未定位到匹配原句，请核对本次材料全文。</p>'}<p>判断原因：${esc(c.reason)}\n修改建议：${esc(c.suggestion)}</p></article>`).join('')}</section><section><h2>8. 最终建议</h2><p>${esc(r.recommendation)}</p></section></article>`;
}
let passedReports = null;
let expandedReport = null;
let historyRequest = 0;
async function loadPassedReports() {
  const request=++historyRequest;
  passedReports=null;expandedReport=null;
  if(state.view==='reports')render();
  try {
    const projects=(await api('/api/projects')).projects||[];
    const histories=await Promise.all(projects.map(async project=>{
      const data=await api(`/api/workbench/projects/${id(project.id)}/runs`);
      return data.runs.filter(run=>USER_MODE||run.result==='pass'||run.qualification?.result==='qualified').map(run=>({...run,project}));
    }));
    if(request!==historyRequest)return;
    passedReports=histories.flat().sort((a,b)=>new Date(b.created_at)-new Date(a.created_at));
  } catch(error) {
    if(request===historyRequest)passedReports={error:true};
    throw error;
  } finally { if(request===historyRequest&&state.view==='reports')render(); }
}
function renderReports() {
  const title=heading('历史记录','保存自动评审通过或人工确认为合格的报告，点击记录展开完整内容。',button('刷新记录','data-refresh-history')+button('返回评审辅助对话','data-view="review"'));
  if(passedReports===null)return title+'<section class="panel empty">正在加载历史记录…</section>';
  if(passedReports.error)return title+'<section class="panel empty">历史记录加载失败，请点击“刷新记录”重试。</section>';
  if(!passedReports.length)return title+'<section class="panel empty"><h2>暂无合格报告记录</h2><p>自动评审通过，或在报告页点击“确认为合格并加入历史记录”后，报告会显示在这里。</p></section>';
  return title+`<p class="muted">共 ${passedReports.length} 条合格报告记录</p><div class="history-list">${passedReports.map(r=>`<section class="panel history-record"><button class="history-summary" data-expand-report="${esc(r.id)}" aria-expanded="${expandedReport?.id===r.id}" aria-controls="history-${esc(r.id)}"><span><strong>${esc(projectName(r.project))}</strong><small>${esc(date(r.created_at))} · ${esc(r.scope_name||'整个项目')}${r.project.id==='DEMO-REVIEW'?' · 演示样例':''}</small></span><span class="row">${badge('pass',r.qualification?'合格 · 人工确认':'已通过 · 自动评审')}<span>${expandedReport?.id===r.id?'收起 ▴':'展开 ▾'}</span></span></button>${expandedReport?.id===r.id?`<div class="history-content" id="history-${esc(r.id)}">${reportContent(expandedReport)}</div>`:''}</section>`).join('')}</div>`;
}
function renderHelp() { return heading('使用指南','用一条主线完成材料评审。')+`<div class="help-grid">${[['材料识别','创建项目、上传材料，在“材料识别”中核对解析状态与分类，并录入本次课题要求。'],['查看评审规则','在规则页查看三类标准、启用状态和版本。可编辑规则或添加自定义规则。'],['执行分类评审','开始评审后，逐条执行当前启用规则。复杂专业要求进入待确认，不由关键词代替专业判断。'],['定位问题与原文','先看摘要，再筛选问题。点击“定位原文”，或点击原文中的批注按钮，查看规则、原因和建议。'],['复核并重新评审','在检查项内填写复核结论、原因和建议。材料、课题要求或规则改变后，点击“重新评审”生成新的记录。'],['输出与归档','报告页支持在线查看、Word 导出及浏览器打印另存 PDF。归档保留当前结论，包含待确认的报告仍明确显示待确认。']].map(([title,text],i)=>`<article class="help-card"><span>0${i+1}</span><h2>${title}</h2><p>${text}</p></article>`).join('')}</div>`; }

async function refreshProjects() { const data=await api('/api/projects'); state.projects=data.projects || []; }
async function selectProject(projectId, view='review') {
  const epoch=++projectEpoch;
  state.busy=false;
  state.project=null;state.run=null;state.materials=null;state.selected=null;state.chunk=null;state.packages=[];state.rules=[];
  state.category='all';state.result='issues';state.step='decision';state.mode='list';
  $('#app').innerHTML='<div class="empty">正在加载项目材料与评审记录…</div>';
  const data=await Promise.all([api(`/api/workbench/projects/${id(projectId)}/materials`),api(`/api/workbench/projects/${id(projectId)}/runs/latest`),api(`/api/workbench/rules?project_id=${id(projectId)}`),api(`/api/projects/${id(projectId)}/material-packages`)]);
  if(epoch!==projectEpoch) return;
  state.materials=data[0];state.project=data[0].project;state.run=data[1];state.rules=data[2].rules;state.packages=data[3].material_packages || [];
  state.scope=state.run?.material_package_id ?? null;
  if(state.scope!==null){const rules=await api(`/api/workbench/rules?project_id=${id(projectId)}&material_package_id=${id(state.scope)}`);if(epoch!==projectEpoch)return;state.rules=rules.rules;}
  if(!state.run) state.step='material';
  localStorage.setItem('review-workbench-project',projectId);if(view!==null)navigate(view);
}
async function refreshMaterial() {
  const projectId=state.project.id;
  const epoch=projectEpoch;
  const [material,packages]=await Promise.all([api(`/api/workbench/projects/${id(projectId)}/materials`),api(`/api/projects/${id(projectId)}/material-packages`)]);
  if(epoch===projectEpoch && state.project?.id===projectId){state.materials=material;state.packages=packages.material_packages||[];}
}
async function runReview(packageId) {
  if(state.busy||!state.project) return;
  if(packageId !== undefined) state.scope=packageId;
  const projectId=state.project.id, epoch=projectEpoch, scope=state.scope;
  state.busy=true;render();
  try {
    const [run,rules]=await Promise.all([api(`/api/workbench/projects/${id(projectId)}/runs`,'POST',{material_package_id:scope}),api(`/api/workbench/rules?project_id=${id(projectId)}${scope!==null?'&material_package_id='+id(scope):''}`)]);
    if(epoch!==projectEpoch || state.project?.id!==projectId) return;
    state.run=run;state.rules=rules.rules;state.selected=null;state.chunk=null;state.step='decision';state.category='all';state.result='issues';state.mode='list';
    await refreshMaterial();if(epoch!==projectEpoch) return;state.view='review';if(USER_MODE)history.replaceState(null,'','#review');toast('评审已完成：结论、规则依据与原文已保存。');
  } finally { if(epoch===projectEpoch){state.busy=false;render();} }
}
function modal(title, body, footer='', formId) {
  $('#editor-body').innerHTML=`${formId?`<form id="${formId}">`:''}<div class="modal-head"><h2>${esc(title)}</h2><button type="button" data-close aria-label="关闭弹窗">×</button></div><div class="modal-body">${body}<p class="form-error" id="form-error" role="alert"></p></div>${footer?`<div class="modal-footer">${footer}</div>`:''}${formId?'</form>':''}`;
  if(USER_MODE)stripUserHints($('#editor-body'));
  $('#editor').showModal();
}
const field=(label,name,value='',type='input',attrs='')=>`<label class="form-field">${label}${type==='textarea'?`<textarea name="${name}" rows="3" ${attrs}>${esc(value)}</textarea>`:`<input name="${name}" value="${esc(value)}" ${attrs}>`}</label>`;
function stepHelp(step) {
  const help = {
    material: {
      meaning: '将项目文件整理成可供评审核对的材料，先确认系统读到了哪些内容。',
      actions: ['进入“项目与材料”，创建或选择项目，上传申报书、图纸等文件。', '核对文件分类、资料包归属和解析状态；解析失败的文件需检查格式或补充可读取的版本。', '进入评审辅助对话的“材料识别”，查看已识别的章节和正文，确认关键内容完整。'],
      outcome: '得到材料清单和可检索的正文。上传成功不等于解析完整，应确认关键材料可读取后再开始评审。'
    },
    common: {
      meaning: '检查材料的基本信息、章节和数据表述是否满足通用要求。',
      actions: ['在“评审规则”中选择“通用标准”，确认本次需要启用的规则及判断标准。', '在评审辅助对话选择评审范围，开始评审或重新评审，然后点击“通用标准”。', '逐项展开检查结果，核对标准、原文和原因；需要人工判断时，点击“复核本项”填写结论。'],
      outcome: '得到通用检查项的“符合、不符合、待确认”结果。字段检查仅核验指定文字是否出现，不能据此认定数据或方案正确。'
    },
    special: {
      meaning: '核对汽机、锅炉、电气等专业的技术方案是否满足对应要求。',
      actions: ['先核对项目专业，并在“评审规则”的“专项标准”中确认适用规则。', '生成评审后，在工作台点击“专项标准”，展开检查项并定位相关原文。', '结合规则要求和技术资料核验，点击“复核本项”记录判断原因及修改建议。证据不足时保留“待确认”。'],
      outcome: '得到有依据的专业检查记录。专业规则按明确判据和原文证据形成自动结论，证据不足时待确认；评审人员可复核并修订。'
    },
    topic: {
      meaning: '对照本次任务，检查项目是否覆盖要求的目标、交付成果和验收指标。',
      actions: ['在“项目与材料”中填写并保存课题任务要求，说明目标、成果及验收条件。', '在“评审规则”中选择“课题要求”，配置与本次任务对应的检查规则。', '重新评审后点击“课题要求”，对照任务内容和材料逐项核验，并记录复核结论。'],
      outcome: '得到课题要求的检查结果。未填写任务要求时，此类检查为“待确认”；填写任务文本后仍需配置对应规则并核验。'
    },
    decision: {
      meaning: '汇总通用标准、专项标准和课题要求的检查结果，说明项目当前存在的问题。',
      actions: ['在评审辅助对话点击“综合结论”，先查看总结果及各类问题数量。', '筛选“不符合”和“待确认”，展开问题、查看依据并定位原文；确认后使用“复核本项”保存判断。', '材料或规则发生修改后重新评审，再核对新的综合结论。'],
      outcome: '任一检查项“不符合”，总结果为“不符合”；没有不符合但仍有待确认事项时，总结果为“待确认”。复核保存后，汇总结果同步更新。'
    },
    report: {
      meaning: '将本次检查结果、依据、原文证据和修改建议整理为可查阅和留存的报告。',
      actions: ['进入“评审报告”，核对项目、评审范围、结论和各项问题。', '使用 Word 导出，或通过打印功能另存为 PDF。', '需要固定本次记录时执行归档；之后可从历史评审记录查看，材料修改后可另行生成新评审。'],
      outcome: '得到本次评审报告及可追溯记录。归档会保留当前的不符合项和待确认项，归档本身不代表项目通过评审。'
    }
  };
  const entry = help[step];
  const index = STEPS.findIndex(([key]) => key === step);
  modal(`第 ${index+1} 步 · ${STEPS[index][1]}`, `<section class="step-help-section"><h3>这一步的含义</h3><p>${esc(entry.meaning)}</p></section><section class="step-help-section"><h3>如何操作</h3><ol>${entry.actions.map(action=>`<li>${esc(action)}</li>`).join('')}</ol></section><section class="step-help-section"><h3>完成后得到什么</h3><p>${esc(entry.outcome)}</p></section>`, button('我知道了', 'type="button" data-close', 'primary'));
}
function editRule(ruleId) {
  const rule=state.rules.find(r=>r.id===ruleId);
  const r=rule||{name:'',category:'topic',standard:'',content:'',terms:[],mode:'manual',enabled:true,suggestion:'请补充对应材料和核验依据。',specialty:'全部'};
  modal(rule?'编辑评审规则':'新建自定义规则',`<div class="rule-meta"><span>${rule?`${esc(rule.id)} · v${rule.version}`:'新规则'}</span><span>${rule?.origin==='default'?'系统默认 · 保存后标记为用户自定义':'用户自定义'}</span><span>${rule?.project_id||(!rule&&state.project)?'仅当前项目':'所有适用项目'}</span></div>`+field('规则名称','name',r.name,'input','required maxlength="160"')+`<div class="form-row"><label class="form-field">所属类别<select name="category">${options(CAT,r.category)}</select></label><label class="form-field">是否启用<select name="enabled">${options({true:'启用',false:'停用'},String(r.enabled))}</select></label></div>`+field('判断标准','standard',r.standard,'textarea','required')+field('当前规则内容','content',r.content,'textarea','required')+`<label class="form-field">执行方式<select name="mode">${options({manual:'证据检索 + 按判据语义评审',presence:'字段检查 + 等价表述核验'},r.mode)}</select></label>`+field('核验字段 / 检索词（每行一项）','terms',r.terms.join('\n'),'textarea')+field('修改建议','suggestion',r.suggestion,'textarea','required')+`<label class="form-field">适用专业<select name="specialty">${options({'全部':'全部','汽机':'汽机','锅炉':'锅炉','电气':'电气'},r.specialty)}</select></label><p class="muted" style="font-size:12px">保存后版本递增。当前评审和报告保留原版本，重新评审时应用新版本。</p><input name="rule_id" value="${esc(ruleId||'')}" type="hidden">`,button('取消','type="button" data-close')+'<button class="primary" type="submit">保存规则</button>','rule-form');
}
function ruleSnapshot(checkId) {
  const c=state.run.checks.find(c=>c.id===checkId);if(!c)return;
  const r=c.rule;
  modal('本次评审使用的规则',`<div class="rule-meta"><span>${CAT[r.category]}</span><span>${r.origin==='default'?'系统默认':'用户自定义'} · 已启用</span></div><h3>${esc(r.name)}</h3><div class="detail-pair"><span class="detail-label">判断标准</span><p>${esc(r.standard)}</p></div><div class="detail-pair"><span class="detail-label">当前规则内容</span><p>${esc(r.content)}</p>${ruleContractDetails(r)}</div><p class="muted">更新时间：${esc(date(r.updated_at))}<br>来源：${esc(r.source)}<br>执行方式：${esc(c.method)}</p>${ruleReference(r)}`,button('关闭','data-close')+(USER_MODE?'':button('编辑当前版本',`data-edit-rule="${esc(r.id)}"`,'primary')));
}
function adjudicate(checkId) {
  const c=state.run.checks.find(c=>c.id===checkId);if(!c)return;
  modal('复核检查项',`<h3>${esc(c.rule.name)}</h3><p class="muted">标准：${esc(c.rule.standard)}</p><label class="form-field">评审结果<select name="result">${options(RESULT,c.result)}</select></label>`+field('判断原因（须说明材料实际情况）','reason',c.reason,'textarea','required')+field('修改建议','suggestion',c.suggestion,'textarea','required')+`<input type="hidden" name="check_id" value="${esc(c.id)}">`,button('取消','type="button" data-close')+'<button type="submit" class="primary">保存复核结论</button>','check-form');
}
function selectStep(step) {
  state.step=step;state.selected=null;state.chunk=null;state.category=CAT[step]?step:'all';state.result=CAT[step]?'all':'issues';
  render();$('.workbench-layout')?.scrollIntoView({behavior:'smooth',block:'start'});
}
function locate(checkId,chunkId) {
  const c=state.run.checks.find(c=>c.id===checkId);if(!c)return;
  state.selected=c.id;state.chunk=chunkId||c.evidence[0]?.id||null;state.mode='document';state.category='all';state.result='all';state.step='decision';
  render();if(state.chunk)document.getElementById(`chunk-${state.chunk}`)?.scrollIntoView({block:'nearest'});
}
document.addEventListener('click',event=>{ const b=event.target.closest('button,a');if(!b)return;
  guarded(async()=>{
    if(b.hasAttribute('data-close')) return $('#editor').close();
    if(b.dataset.stepHelp) return stepHelp(b.dataset.stepHelp);
    if(b.dataset.view) return navigate(b.dataset.view);
    if(b.hasAttribute('data-current-report')){
      if(USER_MODE)return openUserReport();
      state.step='report';navigate('review');
      $('.report-retention, .report-toolbar')?.scrollIntoView({block:'start'});
      const report=$('.report-paper');
      if(report){report.tabIndex=-1;report.focus({preventScroll:true});}
      return;
    }
    if(b.hasAttribute('data-refresh-history'))return loadPassedReports();
    if(b.dataset.qualifyRun){
      b.disabled=true;
      try{
        const run=await api(`/api/workbench/runs/${id(b.dataset.qualifyRun)}/qualify`,'POST');
        if(state.run?.id===run.id)state.run=run;
        state.view='reports';history.replaceState(null,'','#reports');
        await loadPassedReports();expandedReport=run;render();window.scrollTo({top:0});
        toast('已人工确认为合格报告并保留到历史记录。');
      }finally{b.disabled=false;}
      return;
    }
    if(b.dataset.expandReport){
      const runId=b.dataset.expandReport;
      if(expandedReport?.id===runId){expandedReport=null;render();return;}
      const request=++historyRequest;
      const run=await api(`/api/workbench/runs/${id(runId)}`);
      if(request!==historyRequest||state.view!=='reports')return;
      expandedReport=run;render();return;
    }
    if(b.dataset.stat){
      if(b.dataset.stat==='projects')return navigate('projects');
      state.step='decision';state.mode='list';state.category='all';state.result=b.dataset.stat;state.selected=null;state.chunk=null;
      navigate('review');
      if(state.run)$('.review-content')?.scrollIntoView({block:'start'});
      return;
    }
    if(b.hasAttribute('data-demo')){ const data=await api('/api/workbench/demo','POST');await refreshProjects();return selectProject(data.project_id); }
    if(b.dataset.project) return selectProject(b.dataset.project);
    if(b.hasAttribute('data-new-project')) return newProjectMode();
    if(b.hasAttribute('data-upload')){if(!state.project)return toast('请先选择项目。');$('#file-upload').click();return;}
    if(b.dataset.addAttachment)return attachmentForm(Number(b.dataset.addAttachment));
    if(b.hasAttribute('data-run')) return runReview();
    if(b.dataset.runPackage) return runReview(Number(b.dataset.runPackage));
    if(b.dataset.step) return selectStep(b.dataset.step);
    if(b.dataset.mode){state.mode=b.dataset.mode;render();return;}
    if(b.hasAttribute('data-reset-filters')){state.category='all';state.result='all';render();return;}
    if(b.dataset.check){state.selected=state.selected===b.dataset.check?null:b.dataset.check;state.chunk=null;render();return;}
    if(b.dataset.locate) return locate(b.dataset.locate);
    if(b.dataset.evidence) return locate(b.dataset.forCheck,Number(b.dataset.evidence));
    if(b.dataset.annotation) return locate(b.dataset.annotation,Number(b.dataset.chunk));
    if(b.dataset.ruleSnapshot) return ruleSnapshot(b.dataset.ruleSnapshot);
    if(b.dataset.editRule){$('#editor').close();return editRule(b.dataset.editRule);}
    if(b.hasAttribute('data-new-rule'))return editRule();
    if(b.dataset.adjudicate)return adjudicate(b.dataset.adjudicate);
    if(b.hasAttribute('data-knowledge')){state.knowledge=await api('/api/knowledge-base');render();return;}
    if(b.hasAttribute('data-history')){
      if(!state.project)return toast('请先选择项目。');
      const projectId=state.project.id, epoch=projectEpoch;
      const [history,legacy]=await Promise.allSettled([api(`/api/workbench/projects/${id(projectId)}/runs`),api(`/api/projects/${id(projectId)}/review-session/latest`)]);
      if(epoch!==projectEpoch)return;
      if(history.status==='rejected')throw history.reason;
      const rows=history.value.runs;
      const old=legacy.status==='fulfilled'?legacy.value:null;
      modal('历史评审记录',rows.map(r=>`<div class="row spread"><div><strong>${esc(r.id)}</strong><p class="muted">${esc(date(r.created_at))} · ${esc(r.scope_name||'整个项目')} · ${esc(runTitle(r))} · ${r.status==='archived'?'已归档':'已完成'}</p></div>${button('查看',`data-history-run="${esc(r.id)}"`)}</div>`).join('')+(old?`<details><summary>改版前评审记录</summary><p>历史专家结论：${esc(old.expert_conclusion||old.ai_conclusion)}<br>${esc(old.expert_note||'')}</p>${(old.issues||[]).map(i=>`<p>${esc(i.title)}：${esc(i.expert_opinion||i.ai_opinion)}</p>`).join('')}${old.report?`<a href="${esc(API+old.report.download_url)}" target="_blank" rel="noopener">下载历史 Word 报告</a>`:''}</details>`:'')||'<p>当前项目暂无历史评审记录。</p>');return;
    }
    if(b.dataset.historyRun){if(USER_MODE){$('#editor').close();return openUserRun(b.dataset.historyRun,'reports');}const epoch=projectEpoch;const run=await api(`/api/workbench/runs/${id(b.dataset.historyRun)}`);if(epoch!==projectEpoch)return;state.run=run;state.scope=run.material_package_id;state.selected=null;state.chunk=null;$('#editor').close();state.step='report';navigate('review');return;}
    if(b.hasAttribute('data-export')){const runId=(state.view==='reports'?expandedReport:state.run).id;const response=await fetch(`${API}/api/workbench/runs/${id(runId)}/report.docx`);if(!response.ok)throw new Error('报告导出未完成，请重试。');const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=`评审报告-${runId}.docx`;link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);toast('Word 评审报告已导出。');return;}
    if(b.hasAttribute('data-print')){window.print();return;}
    if(b.hasAttribute('data-archive')){if(USER_MODE){const runId=(state.view==='reports'?expandedReport:state.run).id;const run=await api(`/api/workbench/runs/${id(runId)}/archive`,'POST');if(state.run?.id===runId)state.run=run;await openUserReport(run);toast('本次评审已归档。');return;}const epoch=projectEpoch,runId=state.run.id;const run=await api(`/api/workbench/runs/${id(runId)}/archive`,'POST');if(epoch!==projectEpoch||state.run?.id!==runId)return;state.run=run;render();toast('本次评审已归档，当前结论及待确认项已保留。');return;}
    if(b.dataset.saveSource){const sid=b.dataset.saveSource;const category=$(`[data-source-category="${CSS.escape(sid)}"]`).value;await api(`/api/projects/${id(state.project.id)}/sources/${sid}/category`,'PATCH',{category});await refreshMaterial();render();toast('资料分类已保存，重新评审后更新结论。');return;}
  });
});
document.addEventListener('change',event=>{
  if(event.target.id==='review-scope'){state.scope=event.target.value?Number(event.target.value):null;}
  if(event.target.id==='category-filter'){state.category=event.target.value;state.selected=null;state.chunk=null;render();}
  if(event.target.id==='result-filter'){state.result=event.target.value;state.selected=null;state.chunk=null;render();}
  if(event.target.id==='rule-category'){state.rulesCategory=event.target.value;render();}
  if(!USER_MODE&&event.target.id==='admin-rule-project')guarded(async()=>{const projectId=event.target.value;state.project=state.projects.find(p=>p.id===projectId)||null;state.rules=(await api(`/api/workbench/rules${projectId?'?project_id='+id(projectId):''}`)).rules;state.rulesSearch='';render();});
});
document.addEventListener('input',event=>{if(event.target.id==='rule-search'){const pos=event.target.selectionStart;state.rulesSearch=event.target.value;render();$('#rule-search').focus();$('#rule-search').setSelectionRange(pos,pos);}});
document.addEventListener('submit',async event=>{
  const form=event.target;event.preventDefault();if(Number(form.dataset.briefPending)>0)return;const values=Object.fromEntries(new FormData(form));const submit=form.querySelector('[type="submit"]');if(submit)submit.disabled=true;
  try {
    if(form.id==='model-settings-form')await saveModelSettings(values);
    if(form.id==='single-report-form')await submitSingleReport(form);
    if(form.id==='batch-project-form')await submitBatchProjects(form);
    if(form.id==='attachment-form'){const file=new FormData(form).get('attachment');if(!file?.size)throw new Error('请选择一份非空附件。');await uploadProjectFile(file,Number(values.material_package_id));$('#editor').close();toast('附件已补充，请重新评审更新结果。');}
    if(form.id==='project-form'){const data=new FormData();data.append('name',values.name.trim());data.append('project_type',values.type);if(!values.name.trim())throw new Error('请输入项目名称。');const p=await api('/api/projects','POST',data);$('#editor').close();await refreshProjects();await selectProject(p.project_id||p.id);toast('项目已创建，请上传材料并录入课题要求。');}
    if(form.id==='rule-form'){const r=state.rules.find(r=>r.id===values.rule_id);const payload={name:values.name,category:values.category,standard:values.standard,content:values.content,enabled:values.enabled==='true',mode:values.mode,terms:values.terms.split('\n'),suggestion:values.suggestion,specialty:values.specialty,project_id:r?r.project_id||null:state.project?.id||null};await api(`/api/workbench/rules${r?'/'+id(r.id):''}`,r?'PUT':'POST',payload);state.rules=(await api(`/api/workbench/rules${state.project?'?project_id='+id(state.project.id):''}`)).rules;$('#editor').close();render();toast('规则已保存。重新评审后应用新版本。');}
    if(form.id==='check-form'){const epoch=projectEpoch,runId=state.run.id;const run=await api(`/api/workbench/runs/${id(runId)}/checks/${id(values.check_id)}`,'PATCH',{result:values.result,reason:values.reason,suggestion:values.suggestion});if(epoch!==projectEpoch||state.run?.id!==runId)return;state.run=run;if($('#check-form')===form)$('#editor').close();render();toast('本项已复核，摘要和报告已同步更新。');}
    if(form.id==='brief-form'){await api(`/api/workbench/projects/${id(state.project.id)}/brief`,'PUT',{text:values.text});await refreshMaterial();render();toast('课题要求已保存，请重新评审。');}
  } catch(error) { const target=$('#editor[open] #form-error');if(target)target.textContent=error.message;else toast(error.message); }
  finally {if(submit)submit.disabled=false;}
});
$('#file-upload').addEventListener('change',event=>guarded(async()=>{
  const file=event.target.files[0];event.target.value='';if(!file)return;
  await uploadProjectFile(file);toast('材料已接收，请核对解析状态后评审。');
}));
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
async function init() {
  await window.workspaceReady;
  try {
    await refreshProjects();$('#connection-state').textContent='● 评审服务已连接';
    if(!USER_MODE){state.rules=(await api('/api/workbench/rules')).rules;await navigate(location.hash.slice(1));return;}
    const saved=localStorage.getItem('review-workbench-project');const project=state.projects.find(p=>p.id===saved)||state.projects[0];
    if(project) await selectProject(project.id,USER_MODE?location.hash.slice(1):NAV[location.hash.slice(1)]?location.hash.slice(1):'overview');
    else{state.rules=(await api('/api/workbench/rules')).rules;navigate(location.hash.slice(1));}
  } catch(error) {$('#connection-state').textContent='服务未连接';$('#app').innerHTML=heading('连接评审服务','当前无法读取项目数据。')+`<section class="panel empty"><h2>请启动本机评审服务</h2><p>后端地址：${esc(API)}<br>服务启动后刷新页面，即可读取原有项目和规则。</p>${button('重新连接','data-reconnect','primary')}</section>`;}
}
document.addEventListener('click',e=>{if(e.target.closest('[data-reconnect]'))init();});
init();

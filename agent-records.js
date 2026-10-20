'use strict';
const agentRecords={key:null,logs:[],filter:'all'};
const agentRecordQuery=()=>agentScope()?`?material_package_id=${agentScope()}`:'';
async function showAgentConversations(){
  const key=agentChat.key;
  const data=await api(`/api/workbench/projects/${id(state.project.id)}/agent/conversations${agentRecordQuery()}`);
  if(agentChat.key!==key)return;
  modal('会话历史',`<p class="muted">当前项目与评审范围的历史会话。展开查看消息，点击“继续会话”可恢复对话。</p><div class="conversation-list">${data.conversations.map(c=>`<section class="conversation-record"><div class="row spread wrap"><strong>${esc(c.title)}</strong>${c.conversation_id===data.active_id?'<span class="pill">当前会话</span>':button('继续会话',`data-agent-resume="${esc(c.conversation_id)}" ${agentChat.busy?'disabled':''}`)}</div><p class="muted">开始：${esc(date(c.started_at))}<br>最近更新：${esc(date(c.updated_at))} · ${c.messages.length} 条消息</p><details><summary>查看历史消息</summary>${c.messages.map(m=>`<div class="agent-message ${m.role}"><strong>${m.role==='user'?'你':'评审智能体'} · ${esc(date(m.at))}</strong>${agentReasoning(m)}<div class="agent-answer">${agentText(m.content)}</div>${m.incomplete?'<small>本轮未完成</small>':''}</div>`).join('')||'<p>尚无消息。</p>'}</details>${c.run_ids.length?`<div class="row wrap">${c.run_ids.map((r,i)=>button(`查看关联报告 ${i+1}`,`data-agent-report="${esc(r)}"`,'subtle')).join('')}</div>`:'<small class="muted">暂无已记录的关联报告。</small>'}</section>`).join('')}</div>`,button('关闭','type="button" data-close'));
}
function readableLogDetail(value){
  try{const data=JSON.parse(value);return [data.query&&`检索问题：${data.query}`,data.text&&`课题要求：${data.text}`,data.result&&`建议结论：${RESULT[data.result]||data.result}`,data.reason&&`原因：${data.reason}`,data.suggestion&&`修改建议：${data.suggestion}`].filter(Boolean).join('\n')||'读取当前评审范围。';}catch(_){return value;}
}
async function showAgentLogs(){
  const key=agentChat.key;
  const data=await api(`/api/workbench/projects/${id(state.project.id)}/agent/logs${agentRecordQuery()}`);
  if(agentChat.key!==key)return;
  agentRecords.key=key;agentRecords.logs=data.logs;renderAgentLogs();
}
function renderAgentLogs(){
  const logs=agentRecords.logs.filter(l=>agentRecords.filter==='all'||l.conversation_id===agentChat.conversationId);
  modal('操作日志',`<div class="row spread wrap"><label>查看范围<select id="agent-log-filter">${options({all:'全部会话',current:'当前会话'},agentRecords.filter)}</select></label>${button('刷新日志','data-agent-logs')}</div><p class="muted">记录本项目当前范围内的对话、工具执行和复核建议应用；执行中的记录可刷新查看结果。</p><div class="operation-log-list">${logs.map(l=>`<article class="operation-log"><div class="row spread wrap"><strong>${esc(l.action)}</strong><span class="pill ${l.status==='error'?'fail':l.status==='running'?'pending':'pass'}">${({running:'执行中',success:'成功',error:'失败'})[l.status]||esc(l.status)}</span></div><p class="muted">开始：${esc(date(l.started_at))}<br>结束：${esc(date(l.finished_at))}${l.duration_seconds!==null?` · 耗时 ${l.duration_seconds} 秒`:''}</p><details><summary>操作内容</summary><p class="log-text">${esc(readableLogDetail(l.detail))}</p></details><p class="log-text"><b>执行结果：</b>${esc(l.result||'正在执行…')}</p>${l.run_id?`<p>关联报告：${esc(l.report_name)} · ${esc(date(l.run_created_at))}</p>${button('打开关联报告',`data-agent-report="${esc(l.run_id)}"`,'subtle')}`:'<small class="muted">本次操作尚未关联已生成报告。</small>'}</article>`).join('')||'<div class="empty">暂无操作日志。新增执行后会记录；以前未记录的操作不补造日志。</div>'}</div>`,button('关闭','type="button" data-close'));
}
async function activateAgentConversation(conversationId){
  if(agentChat.busy)return;
  const key=agentChat.key;
  const data=await api(`/api/workbench/projects/${id(state.project.id)}/agent/conversations/activate`,'POST',{conversation_id:conversationId||null,material_package_id:agentScope()});
  if(agentChat.key!==key)return;
  Object.assign(agentChat,{conversationId:data.conversation_id,messages:data.messages,proposals:data.proposals,draft:'',evidence:[],retrieval:null,status:conversationId?'已恢复历史会话，可以继续提问。':'新会话已建立，原会话保留在历史列表中。'});
  $('#editor').close();renderAgent();
}
document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;guarded(async()=>{
  if(b.hasAttribute('data-agent-history'))return showAgentConversations();
  if(b.hasAttribute('data-agent-logs'))return showAgentLogs();
  if(b.hasAttribute('data-agent-new'))return activateAgentConversation(null);
  if(b.dataset.agentResume)return activateAgentConversation(b.dataset.agentResume);
  if(b.dataset.agentReport){const projectId=state.project.id;const run=await api(`/api/workbench/runs/${id(b.dataset.agentReport)}`);if(state.project?.id!==projectId)return;state.run=run;state.scope=run.material_package_id;state.step='report';$('#editor').close();if(USER_MODE)return openUserReport(run);navigate('review');}
});});
document.addEventListener('change',event=>{if(event.target.id==='agent-log-filter'){agentRecords.filter=event.target.value;renderAgentLogs();}});

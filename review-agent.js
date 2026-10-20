'use strict';
const agentChat = {key:null, conversationId:null, messages:[], proposals:[], evidence:[],retrieval:null, busy:false, loading:false, configured:null, status:'', draft:'', request:0};
const agentText=text=>esc(text).replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>');
function agentReasoning(message,index){
  if(message.role!=='assistant')return '';
  return `<details class="agent-reasoning" ${message.reasoning_content?'':'hidden'} ${index===undefined?'':`data-reasoning-index="${index}"`} ${index!==undefined&&message.reasoningOpen?'open':''}><summary>查看模型推理${message.incomplete?'（本轮未完成）':''}</summary><small>模型接口返回的推理内容，供查看；评审结论以工作台记录为准。</small><div class="agent-reasoning-text">${agentText(message.reasoning_content||'')}</div></details>`;
}
function agentScope(){return state.run?state.run.material_package_id:state.scope;}
function mountReviewAgent(){
  const key=`${state.project.id}:${agentScope()??'project'}`;
  if(agentChat.key!==key){
    Object.assign(agentChat,{key,conversationId:null,messages:[],proposals:[],evidence:[],retrieval:null,busy:false,loading:true,configured:null,status:'正在连接评审智能体…',draft:''});
    loadAgentHistory(key,state.project.id,agentScope());
  }
  const container=document.createElement('section');container.id='review-agent';container.className='panel agent-panel';
  const anchor=document.querySelector(USER_MODE?'#user-agent-anchor':'.workbench-layout');anchor.before(container);renderAgent();
}
async function loadAgentHistory(key,projectId,scope){
  const request=++agentChat.request;
  try{
    const data=await api(`/api/workbench/projects/${id(projectId)}/agent${scope?`?material_package_id=${scope}`:''}`);
    if(agentChat.key!==key||agentChat.request!==request)return;
    agentChat.messages=data.messages;agentChat.proposals=data.proposals;agentChat.configured=data.configured;agentChat.conversationId=data.conversation_id;
    agentChat.status=data.configured?(USER_MODE?'就绪':'模型已配置 · 可以开始对话'):'模型服务未配置';
  }catch(error){if(agentChat.key===key)agentChat.status=error.message;}
  finally{if(agentChat.key===key){agentChat.loading=false;renderAgent();}}
}
function renderAgent(){
  const root=document.getElementById('review-agent');if(!root)return;
  const scope=state.run?.scope_name||state.packages.find(p=>p.id===state.scope)?.name||'整个项目';
  root.innerHTML=`<div class="panel-head"><div><h2>审计智能体 · 实时交互</h2><p>当前范围：${esc(scope)}。仅处理当前项目的评审问题，无关内容将拒答；操作结果同步到工作台。</p></div><span class="pill ${agentChat.busy?'pending':''}">${agentChat.busy?'处理中':'对话窗口'}</span></div><div class="agent-history-toolbar row wrap">${button('新建会话',`data-agent-new ${agentChat.busy||agentChat.loading?'disabled':''}`)}${button('会话历史','data-agent-history')}${button('操作日志','data-agent-logs')}</div><div class="agent-layout"><div class="agent-conversation"><div class="agent-messages" role="log" aria-label="评审对话">${agentChat.messages.length?agentChat.messages.map((m,index)=>`<div class="agent-message ${m.role}"><strong>${m.role==='user'?'你':'评审智能体'}${m.at?' · '+esc(date(m.at)):''}</strong>${agentReasoning(m,index)}<div class="agent-answer">${agentText(m.content)}</div>${m.incomplete?'<small>本轮响应未完成</small>':''}</div>`).join(''):'<div class="agent-welcome"><h3>从当前报告的一个问题开始</h3><p>例如：先帮我检查这份报告，告诉我还缺什么；或解释一个待确认项，并查找对应原文。</p></div>'}</div><div class="agent-status" role="status">${esc(agentChat.status)}</div><form id="agent-chat-form"><label class="sr-only" for="agent-message">向评审智能体提问</label><textarea id="agent-message" rows="3" placeholder="输入问题或操作要求；Enter 发送，Shift+Enter 换行" ${agentChat.loading?'disabled':''}>${esc(agentChat.draft)}</textarea><div class="row spread wrap"><small class="muted">${agentChat.configured===false?'请配置模型服务后发送；不会使用预设话术代答。':'回复按模型输出实时显示，专业结论修改需点击应用建议。'}</small><button type="submit" class="primary" ${agentChat.busy||agentChat.loading||agentChat.configured!==true?'disabled':''}>${agentChat.busy?'正在处理…':'发送'}</button></div></form></div><aside class="agent-actions"><h3>可以这样开始</h3>${['检查当前报告，指出最需要处理的问题','解释当前待确认项，并检索对应证据','根据当前问题，告诉我需要补充哪些材料'].map(text=>button(esc(text),`data-agent-prompt="${esc(text)}"`,'secondary')).join('')}<h3>本轮原文证据</h3>${retrievalNote(agentChat.retrieval)}${agentChat.evidence.map(e=>`<details><summary>${esc(e.filename)} · 段落 ${e.paragraph_id}</summary><p>${esc(evidenceLabel(e))}</p><p>${esc(e.semantic_quotes?.map(q=>q.quote).join('\n')||e.text)}</p></details>`).join('')||'<p class="muted">检索原文后在这里显示出处。</p>'}<h3>待应用的复核建议</h3>${agentChat.proposals.filter(p=>p.kind!=='batch_audit').map(p=>`<article class="agent-proposal"><strong>${esc(p.name)}</strong><p>建议结论：${esc(RESULT[p.update.result])}</p><p>${esc(p.update.reason)}</p><p>建议：${esc(p.update.suggestion)}</p>${button(p.applied?'已应用':'应用这条复核建议',`data-agent-apply="${esc(p.id)}" ${p.applied||agentChat.busy?'disabled':''}`,'primary')}</article>`).join('')||'<p class="muted">智能体提出建议后，可核对依据并应用。</p>'}</aside></div>`;
  if(USER_MODE){
    root.querySelector('.panel-head h2').textContent='评审助手';
    root.querySelector('.panel-head p').remove();
    root.querySelector('[data-agent-logs]').remove();
    if(agentChat.configured===false)root.querySelector('#agent-chat-form small').textContent='模型服务未配置';
    else root.querySelector('#agent-chat-form small').remove();
    root.querySelectorAll('.agent-welcome p,.agent-actions > p,.agent-reasoning > small').forEach(note=>note.remove());
  }else root.querySelector('.agent-history-toolbar').insertAdjacentHTML('beforeend', `${button('复核当前项',`data-audit-current ${agentChat.busy||!state.selected?'disabled':''}`)}${button('批量检测待确认／不符合项',`data-audit-select ${agentChat.busy||!state.run||state.run.status==='archived'?'disabled':''}`,'primary')}`);
  root.insertAdjacentHTML('beforeend', renderAuditProposals());
  const log=root.querySelector('.agent-messages');log.scrollTop=log.scrollHeight;
}
async function sendAgentMessage(message){
  if(agentChat.busy||agentChat.loading||!message.trim())return;
  const key=agentChat.key,projectId=state.project.id,scope=agentScope(),runId=state.run?.id||null;
  const responseMessage={role:'assistant',content:'',reasoning_content:'',reasoningOpen:false,at:new Date().toISOString()};
  agentChat.messages.push({role:'user',content:message,at:new Date().toISOString(),run_id:runId},responseMessage);agentChat.busy=true;agentChat.draft='';agentChat.evidence=[];agentChat.retrieval=null;agentChat.status='正在发送…';renderAgent();
  try{
    const response=await fetch(`${API}/api/workbench/projects/${id(projectId)}/agent/chat`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,run_id:runId,material_package_id:scope})});
    if(!response.ok){const error=await response.json();throw new Error(error.detail||'发送失败，请重试。');}
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',doneEvent=false;
    const consume=event=>{
      if(agentChat.key!==key)return;
      if(event.type==='status')agentChat.status=event.text;
      if(event.type==='delta'){responseMessage.content+=event.text;agentChat.status='智能体正在回复…';}
      if(event.type==='reasoning_delta'){responseMessage.reasoning_content+=event.text;agentChat.status='模型正在推理，可点击展开查看…';}
      if(event.type==='error'){responseMessage.incomplete=true;agentChat.status=event.text;}
      if(event.type==='tool')agentChat.status=`${event.name}：${event.detail}`;
      if(event.type==='evidence'){agentChat.evidence=event.items;agentChat.retrieval=event.retrieval;}
      if(event.type==='proposal')agentChat.proposals.push(event.proposal);
      if(event.type==='brief'&&state.project?.id===projectId&&state.materials)state.materials.brief=event.text;
      if(event.type==='run'&&state.project?.id===projectId){state.run=event.run;state.scope=event.run.material_package_id;state.step='decision';state.selected=null;render();}
      if(event.type==='done'){doneEvent=true;if(!responseMessage.incomplete)agentChat.status='本轮完成，可以继续追问。';}
      if(['status','delta','reasoning_delta','tool','error','done'].includes(event.type)){
        const root=document.getElementById('review-agent');
        if(root){const bubbles=root.querySelectorAll('.agent-message.assistant');const bubble=bubbles[bubbles.length-1];if(bubble){bubble.querySelector('.agent-answer').innerHTML=agentText(responseMessage.content);const reasoning=bubble.querySelector('.agent-reasoning');reasoning.hidden=!responseMessage.reasoning_content;reasoning.querySelector('.agent-reasoning-text').innerHTML=agentText(responseMessage.reasoning_content);}root.querySelector('.agent-status').textContent=agentChat.status;const log=root.querySelector('.agent-messages');if(!root.querySelector('.agent-reasoning[open]'))log.scrollTop=log.scrollHeight;}
      }else renderAgent();
    };
    while(true){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true});let end;while((end=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,end);buffer=buffer.slice(end+1);if(line.trim())consume(JSON.parse(line));}}
    buffer+=decoder.decode();if(buffer.trim())consume(JSON.parse(buffer));
    if(!doneEvent&&agentChat.key===key)throw new Error('连接中断，本轮回复可能未完成。请核对工作台结果后重试。');
  }catch(error){if(agentChat.key===key){agentChat.status=error.message;responseMessage.incomplete=true;if(!responseMessage.content)responseMessage.content='未能完成本轮对话。';}}
  finally{if(agentChat.key===key){agentChat.busy=false;renderAgent();}}
}
document.addEventListener('submit',event=>{if(event.target.id!=='agent-chat-form')return;event.preventDefault();sendAgentMessage(agentChat.draft);});
document.addEventListener('toggle',event=>{const box=event.target;if(!box.matches?.('[data-reasoning-index]'))return;const message=agentChat.messages[Number(box.dataset.reasoningIndex)];if(message)message.reasoningOpen=box.open;},true);
document.addEventListener('input',event=>{if(event.target.id==='agent-message')agentChat.draft=event.target.value;});
document.addEventListener('keydown',event=>{if(event.target.id==='agent-message'&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();sendAgentMessage(event.target.value);}});
document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;
  if(b.dataset.agentPrompt){agentChat.draft=b.dataset.agentPrompt;renderAgent();document.getElementById('agent-message').focus();}
  if(b.dataset.agentApply)guarded(async()=>{
    b.disabled=true;const key=agentChat.key;
    try{const data=await api(`/api/workbench/projects/${id(state.project.id)}/agent/apply`,'POST',{proposal_id:b.dataset.agentApply,material_package_id:agentScope()});
      if(agentChat.key!==key)return;state.run=data.run;agentChat.proposals=agentChat.proposals.map(p=>p.id===data.proposal.id?data.proposal:p);agentChat.messages.push({role:'user',content:`已应用复核建议：${data.proposal.name}`});agentChat.status='建议已应用，工作台结论已同步更新。';render();
    }finally{b.disabled=false;}
  });
});

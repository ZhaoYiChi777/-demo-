'use strict';
function renderAuditProposals(){
  return agentChat.proposals.filter(p=>p.kind==='batch_audit'&&p.run_id===state.run?.id).slice(-3).reverse().map(p=>`<section class="panel-body audit-results" data-audit-proposal="${esc(p.id)}"><h3>专项复核结果${p.applied?' · 已应用':''}</h3><p>${esc(p.applied?p.summary.replace('尚未修改评审结果。',''):p.summary)}</p>${p.policy?`<details class="audit-policy"><summary>${esc(p.policy.name)} · 查看复核依据</summary><ul>${p.policy.principles.map(t=>`<li>${esc(t)}</li>`).join('')}</ul></details>`:''}<div class="audit-item-list">${p.rows.map(r=>`<details><summary><label><input type="checkbox" data-audit-choice="${esc(r.check_id)}" ${r.eligible&&!p.applied?'checked':'disabled'}> ${esc(r.name)} · ${RESULT[r.before]} → ${RESULT[r.after]}</label></summary><p>${esc(r.reason)}</p><p>${esc(r.suggestion)}</p>${r.judgment.criteria.map(c=>`<article><b>${esc(c.criterion)} · ${RESULT[c.result]}</b><p>${esc(c.reason)}</p>${(c.evidence||[]).map(e=>{const original=r.evidence.find(x=>x.id===e.paragraph_id);return `<blockquote>${esc(e.quote)}<br><small>${esc(original?.filename)} · ${original?.page?'第'+original.page+'页 · ':''}段落 ${e.paragraph_id}</small></blockquote>`;}).join('')}</article>`).join('')}</details>`).join('')}</div>${p.applied?`<p>已应用 ${p.applied_ids.length} 项；其余项可按最新结果继续复核。</p>`:button('确认通过所选项／排除所选不适用项',`data-audit-apply="${esc(p.id)}" ${agentChat.busy||!p.rows.some(r=>r.eligible)?'disabled':''}`,'primary')}<small>只更新所选检查项；整体结论和得分同步重算，合格报告归档仍使用报告页按钮。</small></section>`).join('');
}
function selectAuditChecks(){
  const checks=state.run?.checks.filter(c=>['pending','fail'].includes(c.result))||[];
  if(!checks.length){toast('当前没有待确认或不符合项。');return;}
  modal('选择需要检测的检查项',`<p>当前报告：${esc(state.run.scope_name)}。使用待确认专项复核口径：先核对适用范围，再按原判据补查证据；区分可研阶段要求、字段填写和数值一致性。两次独立判定后可查看依据并批量确认，真实缺证仍保留待确认。</p><div class="row wrap">${button('全选','type="button" data-audit-filter="all"')}${button('仅待确认','type="button" data-audit-filter="pending"')}${button('仅不符合','type="button" data-audit-filter="fail"')}</div><div class="audit-item-list">${checks.map(c=>`<label class="audit-select-row"><input type="checkbox" name="audit_check" value="${esc(c.id)}" data-verdict="${c.result}" checked> ${esc(c.rule.name)} · ${RESULT[c.result]}</label>`).join('')}</div>`, '<button type="submit" class="primary">开始检测所选项</button>','audit-selection-form');
}
async function startCheckAudit(checkIds){
  if(agentChat.busy)return;
  const run=state.run,key=agentChat.key;
  agentChat.busy=true;agentChat.status=`正在复核 ${checkIds.length} 项：全范围检索原文，再逐项独立核验，请稍候…`;renderAgent();
  try{
    const result=await api(`/api/workbench/projects/${id(run.project_id)}/agent/audit`,'POST',{run_id:run.id,material_package_id:run.material_package_id,check_ids:checkIds});
    if(agentChat.key!==key)return;
    agentChat.proposals.push(result.proposal);agentChat.messages.push({role:'assistant',content:result.proposal.summary});agentChat.status=result.proposal.summary;
  }catch(e){if(agentChat.key===key)agentChat.status=e.message;}
  finally{if(agentChat.key===key){agentChat.busy=false;renderAgent();}}
}
document.addEventListener('submit',e=>{
  if(e.target.id!=='audit-selection-form')return;e.preventDefault();
  const selected=[...e.target.querySelectorAll('[name="audit_check"]:checked')].map(x=>x.value);
  if(!selected.length){e.target.querySelector('#form-error').textContent='请至少选择一项。';return;}
  document.getElementById('editor').close();startCheckAudit(selected);
});
document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.hasAttribute('data-audit-select'))selectAuditChecks();
  if(b.hasAttribute('data-audit-current')){
    const check=state.run?.checks.find(c=>c.id===state.selected);
    if(!check||!['pending','fail'].includes(check.result)){toast('请选择一个待确认或不符合项。');return;}
    startCheckAudit([check.id]);
  }
  if(b.dataset.auditFilter)document.querySelectorAll('[name="audit_check"]').forEach(c=>c.checked=b.dataset.auditFilter==='all'||c.dataset.verdict===b.dataset.auditFilter);
  if(b.dataset.auditApply)guarded(async()=>{
    const box=b.closest('[data-audit-proposal]'),selected=[...box.querySelectorAll('[data-audit-choice]:checked:not(:disabled)')].map(c=>c.dataset.auditChoice);
    if(!selected.length){toast('请至少选择一条可应用建议。');return;}
    const key=agentChat.key;agentChat.busy=true;renderAgent();
    try{
      const data=await api(`/api/workbench/projects/${id(state.project.id)}/agent/audit/apply`,'POST',{proposal_id:b.dataset.auditApply,material_package_id:agentScope(),check_ids:selected});
      if(agentChat.key!==key)return;state.run=data.run;
      agentChat.proposals=agentChat.proposals.map(p=>p.id===data.proposal.id?data.proposal:p);
      agentChat.status=`已应用 ${selected.length} 项复核结果，整体结论与得分已重算。`;render();
    }finally{if(agentChat.key===key){agentChat.busy=false;renderAgent();}}
  });
});

'use strict';
function briefUpload(target){
  return `<div class="brief-upload"><label class="form-field">从课题文件导入<input type="file" data-brief-upload="${target}" aria-describedby="brief-purpose-${target}" accept=".docx,.doc,.pdf,.txt,.md" multiple></label><p class="brief-purpose muted" id="brief-purpose-${target}">课题文件用于提供任务目标、交付成果和验收要求，作为核对待审报告是否满足课题要求的依据。导入内容可在上方核对、编辑。</p><small class="muted">支持 Word、PDF、TXT、Markdown，可选择多份。解析内容追加到上方要求中，核对后可编辑；文件不会作为待审报告上传。旧版 DOC、扫描 PDF 需本机转换或识别能力。</small><p class="brief-import-status" role="status"></p></div>`;
}
document.addEventListener('change',async event=>{
  const input=event.target;if(!input.matches('[data-brief-upload]')||!input.files.length)return;
  const form=input.closest('form'),target=form?.elements.namedItem(input.dataset.briefUpload);
  if(!target)return;
  const files=Array.from(input.files),status=input.closest('.brief-upload').querySelector('.brief-import-status'),submit=form.querySelector('[type=submit]');
  form.dataset.briefPending=String(Number(form.dataset.briefPending||0)+1);
  input.disabled=true;if(submit)submit.disabled=true;
  const imported=[],failed=[];
  try{
    for(const [index,file] of files.entries()){
      status.textContent=`正在解析 ${index+1} / ${files.length}：${file.name}`;
      try{
        const data=new FormData();data.append('file',file);
        const result=await api('/api/workbench/brief/import','POST',data);
        if(!input.isConnected||!form.contains(target))return;
        target.value=[target.value.trim(),`【课题文件：${result.filename}】\n${result.text}`].filter(Boolean).join('\n\n');
        target.dispatchEvent(new Event('input',{bubbles:true}));
        imported.push(`${result.filename}（${result.characters} 字）`);
      }catch(error){failed.push(`${file.name}：${error.message}`);}
    }
    status.textContent=[imported.length?`已导入：${imported.join('；')}。请核对上方内容。`:'',...failed].filter(Boolean).join('\n');
    status.classList.toggle('fail-text',failed.length>0);
  }finally{
    input.disabled=false;input.value='';
    form.dataset.briefPending=String(Math.max(0,Number(form.dataset.briefPending)-1));
    if(submit)submit.disabled=Number(form.dataset.briefPending)>0;
  }
});

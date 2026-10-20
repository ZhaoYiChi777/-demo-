'use strict';
const VISUAL_KINDS={table:'原生表格',image:'图片',chart:'原生图表',drawing:'绘图',formula:'公式',visual_page:'图表候选页'};
const VISUAL_SCAN={not_scanned:'尚未提取图表',complete:'图表提取完成',partial:'部分内容待重试',failed:'图表提取未完成',not_applicable:'无需图表提取'};
let visualAssetView=null;
function visualSourceSummary(source){
  const status=source.asset_scan_status||'not_scanned';
  if(status==='not_applicable')return '';
  return `<small>${esc(VISUAL_SCAN[status])} · ${source.asset_count||0} 项</small>${button('查看图表与引用',`data-visual-source="${source.id}"`,'subtle')}`;
}
async function openVisualAssets(sourceId){
  const epoch=projectEpoch,projectId=state.project.id;
  const data=await api(`/api/projects/${id(projectId)}/sources/${id(sourceId)}/visual-assets`);
  if(epoch!==projectEpoch)return;
  visualAssetView={...data,pendingOnly:false};renderVisualAssets();
}
function renderVisualAssets(){
  const data=visualAssetView;if(!data)return;
  const pending=data.items.filter(a=>a.needs_recognition),items=data.pendingOnly?pending:data.items;
  const labels={preview:'打开图像',original:'原始图片',table:'表格 JSON',csv:'表格 CSV',structure:'原始结构',chart:'图表结构',workbook:'图表数据',text:'本页文本'};
  const content=`<p>已保存 ${data.items.length} 项，其中 ${pending.length} 项待进一步识别。候选内容与原文分开保存，尚未作为已核验的评审依据。</p>${data.error?`<p class="notice">${esc(data.error)}</p>`:''}${data.status==='not_scanned'?'<p class="notice">此材料上传于图表提取功能启用前，重新上传即可生成图表清单。</p>':''}<div class="row wrap">${data.source_url?`<a href="${esc(API+data.source_url)}" target="_blank" rel="noopener">原报告</a>`:''}${data.manifest_url?`<a href="${esc(API+data.manifest_url)}" target="_blank" rel="noopener">下载图表清单</a>`:''}${button('全部内容','data-visual-filter="all"',data.pendingOnly?'secondary':'primary')}${button('待进一步识别','data-visual-filter="pending"',data.pendingOnly?'primary':'secondary')}</div>`+
    items.map(a=>`<section class="detail-pair"><h3>${esc(a.citation_id)} · ${esc(VISUAL_KINDS[a.kind]||a.kind)}</h3><p>${esc(a.citation)}<br><small>${a.needs_recognition?'待识别 / 待核验':'已提取行列，尚未核验内容'} · ${esc(a.source_path)}</small></p>${a.location.page===null?'<p class="muted">Word 按正文块定位，未推断页码。</p>':''}${a.files.preview?`<a href="${esc(API+a.files.preview)}" target="_blank" rel="noopener"><img src="${esc(API+a.files.preview)}" alt="${esc(a.citation)}" loading="lazy" style="max-width:100%;max-height:65vh;object-fit:contain"></a>`:''}${a.rows?`<div class="table-scroll"><table><tbody>${a.rows.slice(0,20).map(row=>`<tr>${row.map(c=>`<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${a.rows.length>20?'<p class="muted">预览前20行，完整数据可从下方下载。</p>':''}`:''}${a.context?`<details><summary>附近文字 / 本页原文</summary><p style="white-space:pre-wrap">${esc(a.context)}</p></details>`:''}<div class="row wrap">${Object.entries(a.files).map(([role,url])=>`<a href="${esc(API+url)}" target="_blank" rel="noopener">${esc(labels[role]||role)}</a>`).join(' · ')} · <a href="${esc(API+a.source_url)}" target="_blank" rel="noopener">原报告</a>${button('复制引用',`data-copy-visual="${a.id}"`,'subtle')}</div></section>`).join('');
  modal('图表与待识别内容',content+(!items.length?'<p class="empty">当前列表没有候选内容。</p>':''),button('关闭','type="button" data-close','primary'));
}
document.addEventListener('click',event=>{
  const element=event.target.closest('button');if(!element)return;
  if(element.dataset.visualSource)guarded(()=>openVisualAssets(element.dataset.visualSource));
  if(element.dataset.visualFilter&&visualAssetView){visualAssetView.pendingOnly=element.dataset.visualFilter==='pending';renderVisualAssets();}
  if(element.dataset.copyVisual&&visualAssetView)guarded(async()=>{
    const asset=visualAssetView.items.find(a=>String(a.id)===element.dataset.copyVisual);
    await navigator.clipboard.writeText(asset.citation);toast('图表引用已复制。');
  });
});

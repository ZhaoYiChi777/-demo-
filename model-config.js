'use strict';
let modelSettings = null;
let modelCatalog = [];
let modelCatalogError = '';
let modelSettingsLoading = false;

async function loadModelSettings() {
  modelSettingsLoading = true;
  if (!modelSettings && state.view === 'models') render();
  try {
    modelSettings = await api('/api/models');
    try { modelCatalog = (await api('/api/models/catalog')).models; modelCatalogError = ''; }
    catch (error) { modelCatalogError = error.message; }
  } finally {
    modelSettingsLoading = false;
    if (state.view === 'models') render();
  }
}

function renderModelSettings() {
  if (!modelSettings) return heading('评审模型', '在当前 API 地址下选择评审使用的大模型。') +
    panel('模型配置', '', `<p>${modelSettingsLoading ? '正在读取配置与模型目录…' : '尚未读取配置。'}</p>${button('重新读取', 'data-model-refresh')}`);
  const providers = {qwen:'通义千问 Qwen', deepseek:'DeepSeek', 'moonshot-ai':'Kimi', 'zhipu-ai':'智谱 GLM'};
  return heading('评审模型', '共用当前 API 地址与密钥，切换后用于新评审、专项复核和辅助对话。') +
    panel('当前连接', '', `<div class="panel-body"><p><strong>当前模型：${esc(modelSettings.model)}</strong></p><p class="muted">API URL：${esc(modelSettings.url)}<br>${modelSettings.configured ? '服务端已配置密钥' : '服务端未配置密钥或模型服务未启用'}</p><p>历史评审保留原结果。文字识别继续使用独立 OCR 模型。</p></div>`) +
    panel('选择模型', '目录来自当前 API 服务；具体调用权限以连接测试为准。',
      `<form id="model-settings-form" class="panel-body gap"><div class="form-row"><label class="form-field">模型系列<select id="model-provider"><option value="">全部系列</option>${options(providers,'')}</select></label><label class="form-field">目录中的模型<select id="model-catalog"><option value="">请选择，或在下方输入模型 ID</option>${modelCatalogOptions()}</select></label></div>` +
      field('模型 ID（可手动填写）', 'model', modelSettings.model, 'input', 'required maxlength="200" id="model-id"') +
      `<p id="model-capability" class="muted">${modelCapability(modelSettings.model)}</p>` +
      (modelCatalogError ? `<p class="notice">${esc(modelCatalogError)}</p>` : `<p class="muted">已读取 ${modelCatalog.length} 个模型。</p>`) +
      `<p id="model-test-result" role="status"></p><div class="row">${button('刷新模型目录','type="button" data-model-refresh')}${button('测试连接','type="button" data-model-test')}<button class="primary" type="submit">保存并使用此模型</button></div><p class="muted">连接测试会发送一条简短请求并产生少量 API 用量，不会切换模型或重跑报告。</p></form>`);
}

function modelCatalogOptions(provider = '') {
  return modelCatalog.filter(m => !provider || m.provider === provider).map(m => `<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('');
}

function modelCapability(model) {
  const info = modelCatalog.find(m => m.id === model);
  if (!info) return '自定义模型将使用当前 API 地址调用。';
  return (info.thinking_only ? '该型号仅支持思考模式，系统会自动开启。' : info.supports_thinking === false ? '该型号不使用思考参数。' : '思考模式按评审与对话场景自动设置。') +
    (info.tool_calling === false ? ' 目录未标注工具调用支持，辅助对话功能需进一步验证。' : '');
}

document.addEventListener('change', event => {
  if (event.target.id === 'model-provider') $('#model-catalog').innerHTML = '<option value="">请选择模型</option>' + modelCatalogOptions(event.target.value);
  if (event.target.id === 'model-catalog' && event.target.value) {
    $('#model-id').value = event.target.value;
    $('#model-capability').textContent = modelCapability(event.target.value);
    $('#model-test-result').textContent = '';
  }
});
document.addEventListener('input', event => {
  if (event.target.id === 'model-id') {
    $('#model-capability').textContent = modelCapability(event.target.value.trim());
    $('#model-test-result').textContent = '';
  }
});
document.addEventListener('click', event => {
  const target = event.target.closest('[data-model-refresh], [data-model-test]');
  if (!target) return;
  guarded(async () => {
    target.disabled = true;
    try {
      if (target.hasAttribute('data-model-refresh')) return await loadModelSettings();
      const model = $('#model-id').value.trim();
      const output = $('#model-test-result');
      output.textContent = '正在测试 ' + model + '…';
      const stillCurrent = () => output.isConnected && $('#model-id')?.value.trim() === model;
      try {
        const result = await api('/api/models/test', 'POST', {model});
        if (stillCurrent()) output.textContent = `${result.model}：JSON连接通过，用时 ${(result.duration_ms / 1000).toFixed(1)} 秒。`;
      } catch (error) { if (stillCurrent()) output.textContent = `${model}：${error.message}`; }
    } finally { target.disabled = false; }
  });
});

async function saveModelSettings(values) {
  modelSettings = await api('/api/models', 'PUT', {model:values.model.trim()});
  render();
  toast(`已切换为 ${modelSettings.model}，下次评审与对话使用新模型。`);
}

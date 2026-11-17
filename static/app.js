'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  models: [],
  voices: [],
  tasks: new Map(), // task_id -> { model, source, timer }
  service: null,
  currentView: 'synthesize',
  lastBlob: null,
};

const batchState = {
  taskId: null,
  sentences: [],
  timer: null,
};

const STATUS_LABEL = {
  installed: '已安装',
  partial: '不完整',
  not_installed: '未安装',
  downloading: '下载中',
};

const SOURCE_LABELS = {
  hf: 'Hugging Face',
  modelscope: 'ModelScope',
};

const BACKEND_LABELS = {
  mlx: 'MLX',
  pytorch: 'PyTorch',
};

const VIEW_TITLES = {
  synthesize: '合成',
  batch: '批量合成',
  models: '模型',
};

/* ---------- 工具 ---------- */
function humanSize(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${value} B` : `${value.toFixed(1)} ${units[unit]}`;
}

function cssEscape(value) {
  return window.CSS && CSS.escape ? CSS.escape(value) : value.replace(/"/g, '\\"');
}

function toast(message, type = 'info', timeout = 3600) {
  const wrap = $('#toast-wrap');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), timeout);
}

function makeButton(label, variant, onClick, size = 'btn-sm') {
  const btn = document.createElement('button');
  btn.className = `btn ${variant} ${size}`;
  btn.type = 'button';
  btn.textContent = label;
  btn.addEventListener('click', onClick);
  return btn;
}

/* ---------- 视图切换 ---------- */
function switchView(name) {
  state.currentView = name;
  $$('.nav-item').forEach((el) => el.classList.toggle('active', el.dataset.view === name));
  $$('.view').forEach((el) => el.classList.toggle('active', el.id === `view-${name}`));
  $('#view-title').textContent = VIEW_TITLES[name] || name;
  if (name === 'models') refreshModels();
}

/* ---------- 服务状态 ---------- */
async function refreshService() {
  const dot = $('#service .dot');
  const text = $('#service .service-text');
  try {
    const res = await fetch('/healthz');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `服务不可用（${res.status}）`);
    state.service = data;
    dot.className = 'dot ok';
    text.textContent = `${data.backend} · ${data.sample_rate} Hz`;
    $('#topbar-meta').textContent = `${data.model} 运行中`;
  } catch (err) {
    state.service = null;
    dot.className = 'dot error';
    text.textContent = err.message || '服务不可用';
    $('#topbar-meta').textContent = '';
  }
}

/* ---------- 合成 ---------- */
function updateCharCount() {
  $('#char-count').textContent = `${$('#text').value.length} 字`;
}

function setMessage(text, isError) {
  const el = $('#message');
  el.textContent = text;
  el.classList.toggle('error', Boolean(isError));
}

function synthParams() {
  return {
    temperature: parseFloat($('#temperature').value),
    top_p: parseFloat($('#top_p').value),
    top_k: parseInt($('#top_k').value, 10),
    response_format: $('#format').value,
  };
}

async function synthesize() {
  const text = $('#text').value.trim();
  if (!text) {
    setMessage('请输入合成文本', true);
    return;
  }
  const voiceId = $('#voice-select').value;
  const file = $('#ref_audio').files[0];
  const refText = $('#ref_text').value.trim();
  if (voiceId && file) {
    setMessage('已保存音色与上传参考音频不能同时使用', true);
    return;
  }
  if (file && !refText) {
    setMessage('使用参考音频时必须填写参考文本', true);
    return;
  }

  const button = $('#synthesize');
  button.disabled = true;
  button.classList.add('btn-loading');
  setMessage('合成中…', false);

  try {
    let res;
    if (voiceId) {
      res = await fetch('/v1/audio/speech', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input: text, voice_id: voiceId, ...synthParams() }),
      });
    } else if (file) {
      const fd = new FormData();
      fd.append('input', text);
      fd.append('ref_audio', file);
      fd.append('ref_text', refText);
      for (const [key, value] of Object.entries(synthParams())) fd.append(key, value);
      res = await fetch('/v1/audio/speech/clone', { method: 'POST', body: fd });
    } else {
      res = await fetch('/v1/audio/speech', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input: text, ...synthParams() }),
      });
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `请求失败（${res.status}）`);
    }

    const blob = await res.blob();
    state.lastBlob = blob;
    const url = URL.createObjectURL(blob);
    $('#player').src = url;
    const download = $('#download');
    download.href = url;
    const ext = blob.type.includes('wav') ? 'wav' : blob.type.split('/')[1] || 'audio';
    download.download = `audio8-${Date.now()}.${ext}`;
    $('#result-panel').hidden = false;
    setMessage('合成完成', false);
  } catch (err) {
    setMessage(err.message || '合成失败', true);
  } finally {
    button.disabled = false;
    button.classList.remove('btn-loading');
  }
}

function reuseAsReference() {
  if (!state.lastBlob) return;
  const file = new File([state.lastBlob], 'reference.wav', {
    type: state.lastBlob.type || 'audio/wav',
  });
  const dt = new DataTransfer();
  dt.items.add(file);
  $('#ref_audio').files = dt.files;
  toast('已填入参考音频，请补全参考文本', 'success');
}

/* ---------- 已保存音色 ---------- */
async function refreshVoices() {
  try {
    const res = await fetch('/api/voices');
    if (!res.ok) throw new Error();
    const data = await res.json();
    state.voices = data.voices || [];
  } catch {
    state.voices = [];
  }
  renderVoiceOptions();
}

function renderVoiceOptions() {
  for (const select of [$('#voice-select'), $('#batch-voice-select')]) {
    if (!select) continue;
    const current = select.value;
    select.innerHTML = '';
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '不使用已保存音色';
    select.appendChild(empty);
    for (const voice of state.voices) {
      const option = document.createElement('option');
      option.value = voice.id;
      option.textContent = voice.exists ? voice.name : `${voice.name}（文件缺失）`;
      select.appendChild(option);
    }
    select.value = current;
  }
  updateVoiceHint();
}

function updateVoiceHint() {
  const select = $('#voice-select');
  const voice = state.voices.find((v) => v.id === select.value);
  $('#ref_audio').disabled = Boolean(voice);
  $('#ref_text').disabled = Boolean(voice);
  $('#voice-hint').textContent = voice ? `将使用已保存音色「${voice.name}」` : '';

  const batchSelect = $('#batch-voice-select');
  const batchVoice = state.voices.find((v) => v.id === batchSelect.value);
  $('#batch-ref-audio').disabled = Boolean(batchVoice);
  $('#batch-ref-text').disabled = Boolean(batchVoice);
}

async function saveVoice() {
  const file = $('#ref_audio').files[0];
  const refText = $('#ref_text').value.trim();
  if (!file || !refText) {
    setMessage('请先填写参考音频与参考文本', true);
    return;
  }
  const name = window.prompt('为这个音色命名：', file.name.replace(/\.[^.]+$/, ''));
  if (!name) return;
  const fd = new FormData();
  fd.append('name', name);
  fd.append('ref_text', refText);
  fd.append('file', file);
  try {
    const res = await fetch('/api/voices', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || '保存失败');
    await refreshVoices();
    $('#voice-select').value = data.id;
    updateVoiceHint();
    toast(`已保存音色「${data.name}」`, 'success');
  } catch (err) {
    setMessage(err.message || '保存失败', true);
  }
}

function deleteVoice() {
  const select = $('#voice-select');
  const voice = state.voices.find((v) => v.id === select.value);
  if (!voice) return;
  $('#modal-title').textContent = '删除音色';
  $('#modal-body').textContent = `确定删除音色「${voice.name}」？此操作不可恢复。`;
  const modal = $('#modal');
  modal.hidden = false;
  $('#modal-confirm').onclick = async () => {
    modal.hidden = true;
    try {
      const res = await fetch(`/api/voices/${encodeURIComponent(voice.id)}`, {
        method: 'DELETE',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || '删除失败');
      await refreshVoices();
      toast(`已删除音色「${voice.name}」`, 'success');
    } catch (err) {
      toast(err.message || '删除失败', 'error');
    }
  };
  $('#modal-cancel').onclick = () => {
    modal.hidden = true;
  };
}

/* ---------- 批量合成 ---------- */
function setBatchMessage(text, isError) {
  const el = $('#batch-message');
  el.textContent = text;
  el.classList.toggle('error', Boolean(isError));
}

function batchParams() {
  return {
    temperature: parseFloat($('#batch-temperature').value),
    top_p: parseFloat($('#batch-top-p').value),
    top_k: parseInt($('#batch-top-k').value, 10),
    response_format: $('#batch-format').value,
  };
}

function renderSentences() {
  const list = $('#sentence-list');
  list.innerHTML = '';
  $('#batch-count').textContent = `${batchState.sentences.length} 句`;
  batchState.sentences.forEach((text, i) => {
    const row = document.createElement('div');
    row.className = 'sentence';
    const index = document.createElement('span');
    index.className = 'sentence-index';
    index.textContent = String(i + 1).padStart(3, '0');
    const body = document.createElement('span');
    body.className = 'sentence-text';
    body.textContent = text;
    body.title = text;
    const status = document.createElement('span');
    status.className = 'sentence-status';
    status.textContent = '等待';
    row.append(index, body, status);
    list.appendChild(row);
  });
}

async function startBatch() {
  const text = $('#batch-text').value.trim();
  if (!text) {
    setBatchMessage('请输入或上传文本', true);
    return;
  }

  const file = $('#batch-file').files[0];
  const voiceId = $('#batch-voice-select').value;
  const refFile = $('#batch-ref-audio').files[0];
  const refText = $('#batch-ref-text').value.trim();
  if (voiceId && (refFile || refText)) {
    setBatchMessage('已保存音色与参考音频不能同时使用', true);
    return;
  }
  if (refFile && !refText) {
    setBatchMessage('使用参考音频时必须填写参考文本', true);
    return;
  }
  if (refText && !refFile) {
    setBatchMessage('参考文本需要配套参考音频', true);
    return;
  }

  const fd = new FormData();
  if (file) fd.append('file', file);
  fd.append('input', text);
  if (voiceId) fd.append('voice', voiceId);
  if (refFile) fd.append('ref_audio', refFile);
  if (refText) fd.append('ref_text', refText);
  for (const [key, value] of Object.entries(batchParams())) fd.append(key, value);

  const btn = $('#batch-start');
  btn.disabled = true;
  btn.classList.add('btn-loading');
  setBatchMessage('创建任务…', false);

  try {
    const res = await fetch('/api/batch', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || '创建失败');

    batchState.taskId = data.task_id;
    batchState.sentences = data.sentences || [];
    renderSentences();
    $('#batch-sentences-panel').hidden = false;
    $('#batch-progress-panel').hidden = false;
    $('#batch-result-actions').hidden = true;
    setBatchMessage(
      `共 ${data.total} 句，开始合成${
        voiceId
          ? `（音色「${(state.voices.find((v) => v.id === voiceId) || {}).name || ''}」克隆）`
          : refFile
            ? '（参考音频克隆）'
            : ''
      }`,
      false
    );
    startBatchPolling();
  } catch (err) {
    setBatchMessage(err.message || '创建失败', true);
  } finally {
    btn.disabled = false;
    btn.classList.remove('btn-loading');
  }
}

function startBatchPolling() {
  const tick = async () => {
    try {
      const res = await fetch(`/api/batch/tasks/${batchState.taskId}`);
      if (!res.ok) return;
      const task = await res.json();
      updateBatchProgress(task);
      if (task.status === 'pending' || task.status === 'running') {
        batchState.timer = setTimeout(tick, 800);
      } else if (task.status === 'done') {
        batchState.timer = null;
        $('#batch-result-actions').hidden = false;
        $('#batch-download').href = `/api/batch/tasks/${batchState.taskId}/download`;
        setBatchMessage(`完成：${task.count} 个音频 + manifest.json`, false);
        toast('批量合成完成', 'success');
      } else if (task.status === 'error') {
        batchState.timer = null;
        setBatchMessage(`失败：${task.error}`, true);
      }
    } catch {
      batchState.timer = setTimeout(tick, 2000);
    }
  };
  tick();
}

function updateBatchProgress(task) {
  const total = task.total || batchState.sentences.length;
  const done = task.done || 0;
  const ratio = total ? Math.min(100, (done / total) * 100) : 0;
  $('#batch-bar').style.width = `${ratio}%`;
  $('#batch-progress-text').textContent = `${done} / ${total}`;
  $('#batch-current').textContent = task.current_text
    ? `第 ${task.current} 句：${task.current_text}`
    : '';

  const rows = $$('#sentence-list .sentence');
  rows.forEach((row, i) => {
    const idx = i + 1;
    row.classList.toggle('done', idx <= done);
    row.classList.toggle('active', idx === task.current && task.status === 'running');
    const status = row.querySelector('.sentence-status');
    if (!status) return;
    if (idx <= done) status.textContent = '完成';
    else if (idx === task.current && task.status === 'running') status.textContent = '合成中';
    else status.textContent = '等待';
  });
}

function resetBatch() {
  if (batchState.timer) clearTimeout(batchState.timer);
  batchState.taskId = null;
  batchState.sentences = [];
  batchState.timer = null;
  $('#batch-text').value = '';
  $('#batch-file').value = '';
  $('#batch-file-name').textContent = '未选择文件';
  $('#batch-ref-audio').value = '';
  $('#batch-ref-text').value = '';
  $('#batch-voice-select').value = '';
  updateVoiceHint();
  $('#batch-progress-panel').hidden = true;
  $('#batch-sentences-panel').hidden = true;
  $('#batch-result-actions').hidden = true;
  setBatchMessage('', false);
}

/* ---------- 模型管理 ---------- */
async function refreshModels() {
  try {
    const res = await fetch('/api/models');
    if (!res.ok) throw new Error();
    const data = await res.json();
    state.models = data.models;
    renderModels();
    renderCachePaths(data.cache_dirs || {});
  } catch {
    toast('加载模型列表失败', 'error');
  }
}

function renderCachePaths(dirs) {
  const panel = $('#model-table').closest('.panel');
  if (!panel) return;
  let el = panel.querySelector('.cache-paths');
  if (!el) {
    el = document.createElement('div');
    el.className = 'cache-paths';
    panel.appendChild(el);
  }
  el.textContent = Object.entries(dirs)
    .map(([source, dir]) => `${SOURCE_LABELS[source] || source}：${dir}`)
    .join('　·　');
}

function renderModels() {
  const table = $('#model-table');

  const selected = {};
  $$('.model-row').forEach((el) => {
    selected[el.dataset.modelName] = el.dataset.source;
  });

  table.innerHTML = '';

  let installed = 0;
  let totalSize = 0;
  for (const model of state.models) {
    if (model.sources.some((s) => s.status === 'installed')) installed += 1;
    totalSize += model.sources.reduce((sum, s) => sum + (s.size || 0), 0);
  }

  table.appendChild(buildModelHead());

  if (!state.models.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '暂无模型';
    table.appendChild(empty);
  }

  for (const model of state.models) {
    const task = [...state.tasks.values()].find((t) => t.model === model.name);
    table.appendChild(
      buildModelRow(model, selected[model.name] || model.default_source, task)
    );
  }

  $('#model-stats').innerHTML = '';
  $('#model-stats').append(
    statNode(`${installed} / ${state.models.length}`, '已安装模型'),
    statNode(humanSize(totalSize), '缓存占用'),
    statNode(String(state.tasks.size), '下载任务')
  );
  const spacer = document.createElement('div');
  spacer.className = 'stats-spacer';
  spacer.appendChild(makeButton('刷新', 'btn-ghost', refreshModels));
  $('#model-stats').appendChild(spacer);

  const badge = $('#model-badge');
  badge.hidden = installed === state.models.length;
  badge.textContent = `${installed}/${state.models.length}`;
}

function statNode(value, label) {
  const box = document.createElement('div');
  box.className = 'stat';
  const v = document.createElement('div');
  v.className = 'stat-value';
  v.textContent = value;
  const l = document.createElement('div');
  l.className = 'stat-label';
  l.textContent = label;
  box.append(v, l);
  return box;
}

function buildModelHead() {
  const head = document.createElement('div');
  head.className = 'model-head';
  ['模型', '语言', '说明', '后端', '来源', '大小', '状态', ''].forEach((text) => {
    const cell = document.createElement('div');
    cell.textContent = text;
    head.appendChild(cell);
  });
  return head;
}

function buildModelRow(model, source, task) {
  const src = model.sources.find((s) => s.source === source) || model.sources[0];
  const row = document.createElement('div');
  row.className = 'model-row';
  row.dataset.modelName = model.name;
  row.dataset.source = src.source;

  // 主信息
  const main = document.createElement('div');
  main.className = 'model-main';
  const name = document.createElement('div');
  name.className = 'model-name';
  name.textContent = model.name;
  const id = document.createElement('div');
  id.className = 'model-id';
  id.textContent = src.model_id;
  main.append(name, id);

  // 已安装时把快照路径附在模型信息下（点击复制）
  if (src.snapshot) {
    const pathBox = document.createElement('div');
    pathBox.className = 'model-path';
    pathBox.textContent = src.snapshot;
    pathBox.title = '点击复制路径';
    pathBox.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(src.snapshot);
        toast('路径已复制', 'success');
      } catch {
        toast('复制失败', 'error');
      }
    });
    main.appendChild(pathBox);
  }

  row.appendChild(main);

  // 语言
  const langs = document.createElement('div');
  langs.className = 'model-cell';
  langs.textContent = model.languages ? `${model.languages} 种` : '—';
  row.appendChild(langs);

  // 说明
  const note = document.createElement('div');
  note.className = 'model-cell';
  note.textContent = model.note || '';
  note.title = model.note || '';
  row.appendChild(note);

  // 后端
  const backend = document.createElement('div');
  backend.className = 'model-cell';
  if (model.backend) {
    const chip = document.createElement('span');
    chip.className = `chip chip-backend-${model.backend}`;
    chip.textContent = BACKEND_LABELS[model.backend] || model.backend;
    backend.appendChild(chip);
  }
  row.appendChild(backend);

  // 来源选择
  const sourceCell = document.createElement('div');
  sourceCell.className = 'model-cell';
  if (model.sources.length > 1) {
    const select = document.createElement('select');
    select.className = 'source-select';
    select.dataset.role = 'source-select';
    for (const item of model.sources) {
      const option = document.createElement('option');
      option.value = item.source;
      const mark = item.status === 'installed' ? ' ✓' : '';
      option.textContent = `${SOURCE_LABELS[item.source] || item.source}${mark}`;
      option.selected = item.source === src.source;
      select.appendChild(option);
    }
    select.addEventListener('change', () => {
      const fresh = state.models.find((m) => m.name === model.name);
      if (fresh) {
        const t = [...state.tasks.values()].find((x) => x.model === model.name);
        const next = buildModelRow(fresh, select.value, t);
        row.replaceWith(next);
      }
    });
    sourceCell.appendChild(select);
  } else {
    sourceCell.textContent = SOURCE_LABELS[src.source] || src.source;
  }
  row.appendChild(sourceCell);

  // 大小
  const size = document.createElement('div');
  size.className = 'model-cell';
  size.textContent = src.status === 'not_installed' ? '未下载' : src.size_human;
  row.appendChild(size);

  // 状态
  const status = document.createElement('div');
  status.className = 'model-cell';
  const statusWrap = document.createElement('span');
  statusWrap.className = 'model-status';
  const dot = document.createElement('span');
  dot.className = `status-dot ${src.status}`;
  const statusText = document.createElement('span');
  const taskForSource = task && task.source === src.source ? task : null;
  if (taskForSource && taskForSource.progress != null) {
    statusText.textContent = `下载中 ${Math.round(taskForSource.progress * 100)}%`;
  } else {
    statusText.textContent = STATUS_LABEL[src.status] || src.status;
  }
  statusWrap.append(dot, statusText);
  status.appendChild(statusWrap);
  row.appendChild(status);

  // 操作
  const actions = document.createElement('div');
  actions.className = 'model-actions';
  const downloading = src.status === 'downloading';
  if (downloading) {
    const busy = makeButton('下载中…', 'btn-ghost', () => {});
    busy.disabled = true;
    actions.appendChild(busy);
  } else if (src.status === 'not_installed') {
    actions.appendChild(makeButton('安装', 'btn-primary', () => installModel(model.name, src.source)));
  } else if (src.status === 'partial') {
    actions.appendChild(makeButton('继续安装', 'btn-primary', () => installModel(model.name, src.source)));
    actions.appendChild(makeButton('删除', 'btn-ghost', () => askRemove(model, src.source)));
  } else {
    actions.appendChild(makeButton('删除', 'btn-ghost', () => askRemove(model, src.source)));
  }
  row.appendChild(actions);

  return row;
}

async function installModel(name, source) {
  try {
    const res = await fetch('/api/models/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: name, source }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || '安装失败');

    state.tasks.set(data.task_id, {
      model: name,
      source,
      progress: null,
      timer: null,
    });
    startModelTaskPolling(data.task_id);
    await refreshModels();
    toast(`开始下载 ${name}（${SOURCE_LABELS[source] || source}）`, 'success');
  } catch (err) {
    toast(err.message || '安装失败', 'error');
  }
}

function startModelTaskPolling(taskId) {
  const tick = async () => {
    try {
      const res = await fetch(`/api/models/tasks/${taskId}`);
      if (!res.ok) return;
      const task = await res.json();
      const record = state.tasks.get(taskId);
      if (!record) return;
      record.progress = task.progress;
      updateModelRow(task);
      if (task.status === 'downloading') {
        record.timer = setTimeout(tick, 1500);
      } else {
        state.tasks.delete(taskId);
        await refreshModels();
        if (task.status === 'done') toast(`${task.model_id} 安装完成`, 'success');
        if (task.status === 'error') toast(`安装失败：${task.message}`, 'error');
      }
    } catch {
      const record = state.tasks.get(taskId);
      if (record) record.timer = setTimeout(tick, 3000);
    }
  };
  tick();
}

function updateModelRow(task) {
  const row = $(
    `.model-row[data-model-name="${cssEscape(task.model_name)}"][data-source="${cssEscape(task.source)}"]`
  );
  if (!row) return;
  const statusText = row.querySelector('.model-status span:last-child');
  if (statusText && task.progress != null) {
    statusText.textContent = `下载中 ${Math.round(task.progress * 100)}%`;
  }
}

function askRemove(model, source) {
  const src = model.sources.find((s) => s.source === source) || model.sources[0];
  $('#modal-title').textContent = '删除模型';
  $('#modal-body').textContent =
    `确定删除 ${model.name}（${SOURCE_LABELS[src.source] || src.source}，${src.model_id}，${src.size_human}）？此操作不可恢复。`;
  const modal = $('#modal');
  modal.hidden = false;

  $('#modal-confirm').onclick = async () => {
    modal.hidden = true;
    try {
      const res = await fetch(
        `/api/models/${encodeURIComponent(model.name)}?source=${encodeURIComponent(src.source)}&yes=true`,
        { method: 'DELETE' }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || '删除失败');
      toast(`已删除 ${model.name}`, 'success');
      await refreshModels();
    } catch (err) {
      toast(err.message || '删除失败', 'error');
    }
  };
  $('#modal-cancel').onclick = () => {
    modal.hidden = true;
  };
}

/* ---------- 初始化 ---------- */
$$('.nav-item').forEach((el) =>
  el.addEventListener('click', () => switchView(el.dataset.view))
);
$('#synthesize').addEventListener('click', synthesize);
$('#clear-text').addEventListener('click', () => {
  $('#text').value = '';
  updateCharCount();
});
$('#text').addEventListener('input', updateCharCount);
$('#text').addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') synthesize();
});
$('#reuse').addEventListener('click', reuseAsReference);
$('#voice-select').addEventListener('change', updateVoiceHint);
$('#batch-voice-select').addEventListener('change', updateVoiceHint);
$('#voice-save').addEventListener('click', saveVoice);
$('#voice-delete').addEventListener('click', deleteVoice);
$('#modal-cancel').addEventListener('click', () => {
  $('#modal').hidden = true;
});

$('#batch-upload').addEventListener('click', () => $('#batch-file').click());
$('#batch-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $('#batch-text').value = await file.text();
  $('#batch-file-name').textContent = file.name;
});
$('#batch-start').addEventListener('click', startBatch);
$('#batch-reset').addEventListener('click', resetBatch);

refreshService();
refreshModels();
refreshVoices();
updateCharCount();

setInterval(() => {
  if (state.currentView === 'models' && state.tasks.size > 0) refreshModels();
}, 2000);

'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const TYPE_LABELS = {
  video: '视频',
  photo: '图片',
  audio: '音频',
  voice: '语音',
  document: '文件',
  text: '文本',
};

const KIND_LABELS = {
  '频道': 'CH',
  '群组': 'GRP',
  '收藏': 'SAV',
  '评论': 'CMT',
};

const TYPE_FILTERS = [
  ['video', '视频'],
  ['photo', '图片'],
  ['document', '文件'],
  ['audio', '音频'],
  ['text', '文本'],
];

const CHECK_ICON = '<svg class="icon"><use href="#i-check"/></svg>';
const ROW_HEIGHT = 48;
const OVERSCAN = 8;
const FULL_PAGE = 2000;
const GALLERY_MIN_TILE = 168;
const GALLERY_TILE_HEIGHT = 184;
const GALLERY_GAP = 12;
const GALLERY_PAD = 24;
const GALLERY_TOP_PAD = 16;
const GALLERY_BOTTOM_PAD = 24;
let scrollTimer = null;
let galleryScrollTimer = null;
let progressTimer = null;
let selectionTimer = null;

const state = {
  configured: false,
  authorized: false,
  account: '',
  config: {},
  dialogs: [],
  subs: [],
  current: null,
  messages: [],
  visibleItems: [],
  listRange: null,
  lastScrollTop: 0,
  galleryLayout: null,
  lastGalleryTop: 0,
  msgMap: new Map(),
  history: new Set(),
  selected: new Set(),
  thumbLoaded: new Set(),
  thumbRequested: new Set(),
  nextOffset: 0,
  hasMore: false,
  loading: false,
  loadAll: false,
  view: 'list',
  pageSize: 200,
  typeFilters: { video: true, photo: true, document: true, audio: true, text: false },
  filter: { keyword: '', date_from: '', date_to: '', size_min: null, size_max: null, undownloaded: false },
  downloading: false,
  dlState: new Map(),
  dlStats: { start: 0, files: 0, bytes: 0 },
  previewMid: null,
  previewItem: null,
  previewLoading: false,
  passwordSubmitted: false,
  dedupePlan: null,
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function fmtSize(size) {
  let value = Number(size || 0);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return idx === 0 ? `${Math.round(value)} B` : `${value.toFixed(1)} ${units[idx]}`;
}

async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.body && typeof opts.body !== 'string') {
    opts.body = JSON.stringify(opts.body);
  }
  opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  const res = await fetch(path, opts);
  let data = {};
  try {
    data = await res.json();
  } catch {
    data = {};
  }
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `请求失败 (${res.status})`);
  }
  return data;
}
function toast(text, kind = 'info', ms = 3400) {
  const stack = $('#toast-stack');
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = text;
  stack.appendChild(node);
  setTimeout(() => {
    node.classList.add('leaving');
    setTimeout(() => node.remove(), 220);
  }, ms);
}

function setStatus(text) {
  $('#status-text').textContent = text;
}

function setRight(text) {
  $('#status-right').textContent = text;
}

function setLive(mode) {
  const dot = $('#live-dot');
  dot.classList.toggle('on', mode === 'on');
  dot.classList.toggle('busy', mode === 'busy');
}

function setLoginStatus(text) {
  $('#login-status-text').textContent = text;
}

function updateAccount() {
  const label = state.account || (state.configured ? '正在连接' : '未配置');
  $('#account-label').textContent = label;
}

document.addEventListener('DOMContentLoaded', () => {
  bindUI();
  renderTypeChips();
  applyTheme(localStorage.getItem('vault-theme') || 'dark');
  init();
});

async function init() {
  try {
    const data = await api('/api/state');
    applyServerState(data);
  } catch (err) {
    toast(`无法连接本地服务：${err.message}`, 'error', 6000);
    setStatus('本地服务未响应');
  }
  connectEvents();
  setStatus('本地控制台已连接');
}

function applyServerState(data) {
  state.configured = !!data.configured;
  state.authorized = !!data.authorized;
  state.account = data.account || '';
  state.config = data.config || {};
  state.dialogs = data.dialogs || [];
  state.subs = data.subs || [];
  state.downloading = !!data.downloading;
  state.pageSize = Number(state.config.page_size || 200);
  $('#page-size').value = String(state.pageSize);
  $('#download-dir').value = state.config.download_dir || '';
  $('#concurrency').value = String(state.config.concurrency || 8);
  updateAccount();
  renderChannels();
  updateStageHeader();
  updateSelection();
  syncLoadButtons();
  if (data.qr) showQR(data.qr);
  if (state.authorized) {
    hideLogin();
  } else {
    showLogin();
  }
  if (!state.configured) {
    setLoginStatus('需要填写 API 信息');
    openSettings();
  }
  if (state.subs.length) {
    setRight(`${state.subs.length} 个追更`);
  }
}
function bindUI() {
  $('#theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    localStorage.setItem('vault-theme', next);
  });
  $('#settings-open').addEventListener('click', openSettings);
  $('#login-settings').addEventListener('click', openSettings);
  $('#refresh-channels').addEventListener('click', refreshDialogs);
  $('#login-refresh').addEventListener('click', () => {
    api('/api/connect', { method: 'POST' }).catch((err) => toast(err.message, 'error'));
    setLoginStatus('正在重新生成二维码…');
  });
  $('#channel-search').addEventListener('input', renderChannels);
  $('#open-folder').addEventListener('click', () => {
    api('/api/open_folder', { method: 'POST' }).catch((err) => toast(err.message, 'error'));
  });

  $$('.view-switch button').forEach((btn) => {
    btn.addEventListener('click', () => setView(btn.dataset.view));
  });

  $('#filter-open').addEventListener('click', openFilter);
  $('#filter-apply').addEventListener('click', applyFilter);
  $('#filter-reset').addEventListener('click', resetFilter);
  $('#open-comments').addEventListener('click', openComments);
  $('#load-more').addEventListener('click', loadMore);
  $('#load-all').addEventListener('click', loadAllMessages);
  $('#dedupe').addEventListener('click', openDedupe);
  $('#dedupe-close').addEventListener('click', () => $('#dedupe-dialog').close());
  $('#dedupe-unselect').addEventListener('click', () => applyDedupe(false));
  $('#dedupe-all').addEventListener('click', downloadAllSelected);
  $('#dedupe-download').addEventListener('click', () => applyDedupe(true));
  $('#reorganize').addEventListener('click', reorganizeFiles);

  $('#page-size').addEventListener('change', (event) => {
    state.pageSize = Number(event.target.value || 200);
    if (state.current) reloadCurrent();
  });

  $('#select-all').addEventListener('click', selectAllVisible);
  $('#clear-selection').addEventListener('click', clearSelection);
  $('#download-start').addEventListener('click', () => startDownload());
  $('#download-stop').addEventListener('click', stopDownload);
  $('#preview-close').addEventListener('click', clearPreview);
  $('#preview-action').addEventListener('click', previewAction);
  $('#preview-download').addEventListener('click', () => {
    if (state.previewItem) startDownload([state.previewItem.id]);
  });
  $('#preview-frame').addEventListener('click', (event) => {
    if (event.target.closest('video, audio, button')) return;
    previewAction();
  });

  $('#subs-open').addEventListener('click', openSubs);
  $('#subs-close').addEventListener('click', () => $('#subs-dialog').close());
  $('#subs-add').addEventListener('click', addSub);

  $('#lightbox-close').addEventListener('click', () => $('#lightbox').close());

  $('#password-submit').addEventListener('click', submitPassword);
  $('#password-dialog').addEventListener('close', () => {
    if (!state.passwordSubmitted) {
      api('/api/connect', { method: 'POST' }).catch(() => {});
    }
    state.passwordSubmitted = false;
  });

  $('#channel-list').addEventListener('click', (event) => {
    const row = event.target.closest('.channel-row');
    if (row) selectChannel(Number(row.dataset.id));
  });

  $('#message-list').addEventListener('click', (event) => {
    const row = event.target.closest('.msg-row');
    if (!row) return;
    const mid = Number(row.dataset.mid);
    if (event.target.closest('[data-act="toggle"]')) {
      toggleSelection(mid);
      return;
    }
    previewMessage(mid);
  });
  $('#message-list').addEventListener('scroll', () => {
    if (state.view !== 'list') return;
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      scrollTimer = null;
      renderListWindow();
    }, 16);
  }, { passive: true });
  $('#gallery').addEventListener('scroll', () => {
    if (state.view !== 'grid') return;
    if (galleryScrollTimer) clearTimeout(galleryScrollTimer);
    galleryScrollTimer = setTimeout(() => {
      galleryScrollTimer = null;
      renderGalleryWindow();
    }, 16);
  }, { passive: true });
  window.addEventListener('resize', () => {
    if (state.view === 'list') {
      state.listRange = null;
      renderListWindow(true);
    } else if (state.view === 'grid') {
      state.galleryLayout = null;
      renderGalleryWindow(true);
    }
  });
  setInterval(() => {
    if (state.view === 'list') {
      const host = $('#message-list');
      if (!host) return;
      const top = host.scrollTop || 0;
      if (top === state.lastScrollTop) return;
      state.lastScrollTop = top;
      renderListWindow();
    } else if (state.view === 'grid') {
      const host = $('#gallery');
      if (!host) return;
      const top = host.scrollTop || 0;
      if (top === state.lastGalleryTop) return;
      state.lastGalleryTop = top;
      renderGalleryWindow();
    }
  }, 120);

  $('#gallery').addEventListener('click', (event) => {
    const tile = event.target.closest('.tile');
    if (!tile) return;
    const mid = Number(tile.dataset.mid);
    if (event.target.closest('[data-act="toggle"]')) {
      toggleSelection(mid);
      return;
    }
    previewMessage(mid);
  });

  $('#subs-list').addEventListener('click', (event) => {
    const btn = event.target.closest('button[data-sub-action]');
    if (!btn) return;
    const row = btn.closest('.sub-row');
    if (!row) return;
    subAction(btn.dataset.subAction, Number(row.dataset.id));
  });

  $('#settings-save').addEventListener('click', saveSettings);

  $('#password-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') submitPassword();
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const use = $('#theme-toggle use');
  use.setAttribute('href', theme === 'dark' ? '#i-moon' : '#i-sun');
}

function showLogin() {
  $('#login-layer').hidden = false;
  $('#app').dataset.auth = 'pending';
}

function hideLogin() {
  $('#login-layer').hidden = true;
  $('#app').dataset.auth = 'ok';
}

function showQR(qr) {
  $('#qr-image').src = qr.image;
  $('#qr-image').hidden = false;
  $('#qr-empty').hidden = true;
  setLoginStatus('等待扫码确认');
}

function updateStageHeader() {
  const channel = state.current;
  if (!channel) {
    $('#channel-name').textContent = '选择一个频道';
    $('#channel-sub').textContent = '从左侧选择频道，加载消息并挑选要下载的内容。';
  } else {
    $('#channel-name').textContent = channel.title;
    const parts = [`${state.messages.length} 条消息`, `${state.selected.size} 已选`, `${state.history.size} 已存`];
    if (channel.kind === '评论') parts.push('评论区');
    $('#channel-sub').textContent = parts.join(' · ');
  }
  const commentBtn = $('#open-comments');
  if (commentBtn) {
    commentBtn.hidden = !(channel && channel.kind === '频道' && channel.has_comments);
  }
  $('#channel-count').textContent = `${state.dialogs.length} 个频道`;
}

function updateSelection() {
  let size = 0;
  for (const mid of state.selected) {
    const item = state.msgMap.get(mid);
    size += item ? Number(item.size || 0) : 0;
  }
  $('#selected-count').textContent = String(state.selected.size);
  $('#selected-size').textContent = fmtSize(size);
  $('#download-start').disabled = state.downloading || state.selected.size === 0 || !state.current;
  $('#download-stop').disabled = !state.downloading;
  $('#stat-filtered').textContent = String(state.visibleItems.length);
  updateStageHeader();
}
function renderChannels() {
  const keyword = $('#channel-search').value.trim().toLowerCase();
  const list = $('#channel-list');
  const items = state.dialogs.filter((d) => !keyword || d.title.toLowerCase().includes(keyword));
  if (!items.length) {
    list.innerHTML = `<p class="rail-empty">${state.dialogs.length ? '没有匹配的频道。' : '频道列表为空。登录后会自动加载。'}</p>`;
    $('#channel-count').textContent = `${state.dialogs.length} 个频道`;
    return;
  }
  list.innerHTML = items.map((item) => `
    <button class="channel-row ${state.current && state.current.id === item.id ? 'active' : ''}" data-id="${item.id}">
      <span class="name">${esc(item.title)}</span>
      <span class="kind">${esc(KIND_LABELS[item.kind] || 'CH')}</span>
    </button>`).join('');
  $('#channel-count').textContent = `${state.dialogs.length} 个频道`;
}

async function refreshDialogs() {
  try {
    await api('/api/refresh_dialogs', { method: 'POST' });
    toast('正在刷新频道列表…');
  } catch (err) {
    toast(err.message, 'error');
  }
}

function selectChannel(id) {
  const channel = state.dialogs.find((item) => item.id === id);
  if (!channel) return;
  state.current = channel;
  state.messages = [];
  state.visibleItems = [];
  state.listRange = null;
  state.msgMap = new Map();
  state.history = new Set();
  state.selected = new Set();
  state.thumbLoaded = new Set();
  state.thumbRequested = new Set();
  state.nextOffset = 0;
  state.hasMore = false;
  state.loadAll = false;
  state.loading = true;
  resetListScroll();
  resetGalleryScroll();
  clearPreview();
  renderChannels();
  updateStageHeader();
  updateSelection();
  renderCurrentView();
  showLoading(true);
  syncLoadButtons();
  api(`/api/history?dialog_id=${id}`)
    .then((data) => {
      if (!state.current || state.current.id !== id) return;
      state.history = new Set(data.mids || []);
      renderCurrentView();
      updateStageHeader();
      updateSelection();
    })
    .catch(() => {});
  requestMessages(0);
}

function reloadCurrent() {
  if (!state.current) return;
  const id = state.current.id;
  state.messages = [];
  state.visibleItems = [];
  state.listRange = null;
  state.msgMap = new Map();
  state.selected = new Set();
  state.thumbLoaded = new Set();
  state.thumbRequested = new Set();
  state.nextOffset = 0;
  state.hasMore = false;
  state.loadAll = false;
  state.loading = true;
  resetListScroll();
  resetGalleryScroll();
  clearPreview();
  updateSelection();
  renderCurrentView();
  showLoading(true);
  syncLoadButtons();
  requestMessages(0);
  api(`/api/history?dialog_id=${id}`)
    .then((data) => {
      if (!state.current || state.current.id !== id) return;
      state.history = new Set(data.mids || []);
      renderCurrentView();
      updateStageHeader();
      updateSelection();
    })
    .catch(() => {});
}

function requestMessages(offset, limit) {
  if (!state.current) return;
  const dialogId = state.current.id;
  state.loading = true;
  syncLoadButtons();
  api('/api/messages', {
    method: 'POST',
    body: { dialog_id: dialogId, offset_id: offset || 0, limit: limit || state.pageSize },
  }).catch((err) => {
    if (!state.current || state.current.id !== dialogId) return;
    state.loading = false;
    state.loadAll = false;
    showLoading(false);
    syncLoadButtons();
    toast(err.message, 'error');
  });
}

function syncLoadButtons() {
  const allBtn = $('#load-all');
  const moreBtn = $('#load-more');
  if (!allBtn || !moreBtn) return;
  allBtn.textContent = state.loadAll ? '停止加载' : '查看全部';
  allBtn.disabled = !state.current;
  moreBtn.disabled = !state.current || !state.hasMore || state.loading;
}

function loadMore() {
  if (!state.current || !state.hasMore || state.loading) return;
  state.loadAll = false;
  requestMessages(state.nextOffset);
}

function loadAllMessages() {
  if (!state.current) return;
  if (state.loadAll) {
    state.loadAll = false;
    setStatus(`已停止加载，当前 ${state.messages.length} 条`);
    syncLoadButtons();
    return;
  }
  if (!state.hasMore) {
    toast(`当前已加载全部 ${state.messages.length} 条消息`, 'info');
    return;
  }
  state.loadAll = true;
  setStatus(`正在查看全部：已加载 ${state.messages.length} 条…`);
  syncLoadButtons();
  if (!state.loading) {
    requestMessages(state.nextOffset, FULL_PAGE);
  }
}

function onMessages(data) {
  if (!state.current || data.dialog_id !== state.current.id) return;
  state.loading = false;
  showLoading(false);
  for (const item of data.items || []) {
    if (!state.msgMap.has(item.id)) {
      state.messages.push(item);
      state.msgMap.set(item.id, item);
    }
  }
  state.nextOffset = data.next_offset || 0;
  state.hasMore = !!data.has_more;
  updateStageHeader();
  renderCurrentView();
  updateSelection();
  if (state.loadAll && state.hasMore) {
    setStatus(`正在查看全部：已加载 ${state.messages.length} 条…`);
    setTimeout(() => {
      if (state.loadAll) requestMessages(state.nextOffset, FULL_PAGE);
    }, 150);
  } else if (state.loadAll) {
    state.loadAll = false;
    setStatus(`已加载全部 ${state.messages.length} 条`);
    toast(`已加载全部 ${state.messages.length} 条消息`, 'success', 5000);
  }
  syncLoadButtons();
}

async function openComments() {
  if (!state.current) return;
  if (state.current.kind !== '频道' || !state.current.has_comments) {
    toast('当前频道没有关联评论区', 'info');
    return;
  }
  setStatus('正在打开评论区…');
  try {
    await api('/api/open_comments', {
      method: 'POST',
      body: { dialog_id: state.current.id },
    });
  } catch (err) {
    toast(err.message, 'error', 6000);
    setStatus('打开评论区失败');
  }
}

function setView(view) {
  state.view = view;
  $$('.view-switch button').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === view));
  renderCurrentView();
}

function visibleItems() {
  const filter = state.filter;
  return state.messages.filter((item) => {
    const key = item.type === 'voice' ? 'audio' : item.type;
    if (!state.typeFilters[key]) return false;
    if (filter.keyword) {
      const haystack = `${item.name || ''} ${item.caption || ''}`.toLowerCase();
      if (!haystack.includes(filter.keyword.toLowerCase())) return false;
    }
    const day = (item.date || '').slice(0, 10);
    if (filter.date_from && day < filter.date_from) return false;
    if (filter.date_to && day > filter.date_to) return false;
    const size = Number(item.size || 0);
    if (filter.size_min != null && size < filter.size_min) return false;
    if (filter.size_max != null && size > filter.size_max) return false;
    if (filter.undownloaded && state.history.has(item.id)) return false;
    return true;
  });
}

function renderTypeChips() {
  const host = $('#type-chips');
  host.innerHTML = TYPE_FILTERS.map(([key, label]) => `
    <button class="type-chip ${state.typeFilters[key] ? 'active' : ''}" data-type="${key}">${label}</button>
  `).join('');
  $$('.type-chip', host).forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.type;
      state.typeFilters[key] = !state.typeFilters[key];
      btn.classList.toggle('active', state.typeFilters[key]);
      resetListScroll();
      resetGalleryScroll();
      renderCurrentView();
      updateSelection();
    });
  });
}
function renderCurrentView() {
  const items = visibleItems();
  state.visibleItems = items;
  const hasItems = items.length > 0;
  const empty = $('#empty-state');
  const listWrap = $('#list-wrap');
  const gallery = $('#gallery');

  if (!state.current) {
    empty.hidden = false;
    empty.innerHTML = `
      <p class="eyebrow">NO CHANNEL SELECTED</p>
      <h3>内容在这里沉淀。</h3>
      <p>选择一个频道后，消息、缩略图与下载状态会出现在这块工作区。</p>`;
    listWrap.hidden = true;
    gallery.hidden = true;
    return;
  }

  if (state.loading && !state.messages.length) {
    empty.hidden = false;
    empty.innerHTML = `
      <p class="eyebrow">READING</p>
      <h3>正在读取 ${esc(state.current.title)}。</h3>
      <p>消息会在几秒内出现。</p>`;
    listWrap.hidden = true;
    gallery.hidden = true;
    return;
  }

  if (!hasItems) {
    empty.hidden = false;
    const filtered = state.messages.length > 0;
    empty.innerHTML = filtered
      ? `<p class="eyebrow">NOTHING MATCHES</p>
         <h3>没有符合条件的内容。</h3>
         <p>调整类型、关键词或日期范围后再看。</p>
         <button class="ghost-button" id="empty-reset" style="margin-top:14px">清空筛选</button>`
      : `<p class="eyebrow">EMPTY CHANNEL</p>
         <h3>这个频道没有可显示的消息。</h3>
         <p>它可能只有文本消息，或当前筛选条件把它们隐藏了。</p>`;
    listWrap.hidden = true;
    gallery.hidden = true;
    const reset = $('#empty-reset');
    if (reset) reset.addEventListener('click', resetFilter);
    return;
  }

  empty.hidden = true;
  if (state.view === 'list') {
    listWrap.hidden = false;
    gallery.hidden = true;
    renderMessages(items);
  } else {
    listWrap.hidden = true;
    gallery.hidden = false;
    renderGallery(items);
  }
  $('#stat-filtered').textContent = String(items.length);
  $('#stat-downloaded').textContent = String(state.history.size);
}

function showLoading(show) {
  $('#loading-strip').hidden = !show;
}

function resetListScroll() {
  const host = $('#message-list');
  if (host) host.scrollTop = 0;
  state.listRange = null;
  state.lastScrollTop = 0;
}

function rowHTML(item) {
  const isSelected = state.selected.has(item.id);
  const isActive = state.previewMid === item.id;
  const saved = state.history.has(item.id);
  const downloadable = !!item.downloadable;
  const label = TYPE_LABELS[item.type] || item.type;
  const text = item.name || item.caption || '（无标题）';
  const stateClass = saved ? 'saved' : isSelected ? 'selected-state' : '';
  const stateText = saved ? '已存' : isSelected ? '已选' : downloadable ? '' : '文本';
  return `<div class="msg-row ${isSelected ? 'selected' : ''} ${isActive ? 'active' : ''}" data-mid="${item.id}">
    <button class="msg-check" data-act="toggle" ${downloadable ? '' : 'disabled'} aria-label="选择">${CHECK_ICON}</button>
    <span class="msg-type">${esc(label)}</span>
    <span class="msg-date">${esc(item.date || '')}</span>
    <span class="msg-size">${fmtSize(item.size)}</span>
    <span class="msg-name">${esc(text)}</span>
    <span class="msg-state ${stateClass}">${stateText}</span>
  </div>`;
}

function renderMessages(items) {
  state.visibleItems = items;
  state.listRange = null;
  renderListWindow(true);
}

function renderListWindow(force = false) {
  const host = $('#message-list');
  if (!host) return;
  const items = state.visibleItems || [];
  const total = items.length;
  if (!total) {
    host.innerHTML = '';
    state.listRange = null;
    return;
  }
  const viewport = host.clientHeight || 600;
  const scrollTop = host.scrollTop || 0;
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const end = Math.min(total, start + Math.ceil(viewport / ROW_HEIGHT) + OVERSCAN * 2);
  if (!force && state.listRange && state.listRange.start === start && state.listRange.end === end) {
    return;
  }
  state.listRange = { start, end };
  const topHeight = start * ROW_HEIGHT;
  const bottomHeight = (total - end) * ROW_HEIGHT;
  const rows = items.slice(start, end).map(rowHTML).join('');
  host.innerHTML = `<div class="virtual-spacer" style="height:${topHeight}px"></div>${rows}<div class="virtual-spacer" style="height:${bottomHeight}px"></div>`;
}

function resetGalleryScroll() {
  const host = $('#gallery');
  if (host) host.scrollTop = 0;
  state.galleryLayout = null;
  state.lastGalleryTop = 0;
}

function galleryLayoutFor(host, total) {
  const width = host.clientWidth || 800;
  const usable = Math.max(1, width - GALLERY_PAD * 2);
  const cols = Math.max(
    1,
    Math.floor((usable + GALLERY_GAP) / (GALLERY_MIN_TILE + GALLERY_GAP))
  );
  const tileW = (usable - GALLERY_GAP * (cols - 1)) / cols;
  const rows = Math.ceil(total / cols);
  const totalHeight = rows
    ? GALLERY_TOP_PAD + rows * GALLERY_TILE_HEIGHT + (rows - 1) * GALLERY_GAP + GALLERY_BOTTOM_PAD
    : 0;
  return { width, cols, tileW, rows, total, totalHeight };
}

function tileHTML(item, index, layout) {
  const isSelected = state.selected.has(item.id);
  const isActive = state.previewMid === item.id;
  const saved = state.history.has(item.id);
  const label = TYPE_LABELS[item.type] || item.type;
  const canThumb = ['photo', 'video', 'document'].includes(item.type);
  const row = Math.floor(index / layout.cols);
  const col = index % layout.cols;
  const left = GALLERY_PAD + col * (layout.tileW + GALLERY_GAP);
  const top = GALLERY_TOP_PAD + row * (GALLERY_TILE_HEIGHT + GALLERY_GAP);
  const img = canThumb
    ? `<img data-mid="${item.id}" src="${thumbUrl(item.id, state.current ? state.current.id : 0)}" decoding="async" alt="">`
    : '';
  return `<div class="tile ${isSelected ? 'selected' : ''} ${isActive ? 'active' : ''}" data-mid="${item.id}"
    style="left:${left}px;top:${top}px;width:${layout.tileW}px;height:${GALLERY_TILE_HEIGHT}px">
    <div class="tile-fallback">${esc(label)}</div>
    ${img}
    <button class="tile-check" data-act="toggle" ${item.downloadable ? '' : 'disabled'} aria-label="选择">${CHECK_ICON}</button>
    ${saved ? '<span class="tile-saved">已存</span>' : ''}
    <div class="tile-meta"><span class="t">${esc(label)}</span><span>${fmtSize(item.size)}</span></div>
  </div>`;
}

function renderGallery(items) {
  state.visibleItems = items;
  state.galleryLayout = null;
  renderGalleryWindow(true);
}

function renderGalleryWindow(force = false) {
  const host = $('#gallery');
  if (!host) return;
  const items = state.visibleItems || [];
  const total = items.length;
  if (!total) {
    host.innerHTML = '';
    state.galleryLayout = null;
    return;
  }
  const base = galleryLayoutFor(host, total);
  const rowH = GALLERY_TILE_HEIGHT + GALLERY_GAP;
  const viewport = host.clientHeight || 600;
  const scrollTop = host.scrollTop || 0;
  const startRow = Math.max(0, Math.floor((scrollTop - GALLERY_TOP_PAD) / rowH) - 1);
  const endRow = Math.min(base.rows, startRow + Math.ceil(viewport / rowH) + 3);
  const startIndex = Math.min(total, startRow * base.cols);
  const endIndex = Math.min(total, endRow * base.cols);
  const prev = state.galleryLayout;
  if (
    !force && prev &&
    prev.width === base.width &&
    prev.total === total &&
    prev.startIndex === startIndex &&
    prev.endIndex === endIndex
  ) {
    return;
  }
  const layout = { ...base, startIndex, endIndex };
  state.galleryLayout = layout;
  const tiles = items
    .slice(startIndex, endIndex)
    .map((item, i) => tileHTML(item, startIndex + i, layout))
    .join('');
  host.innerHTML = `<div class="gallery-inner" style="height:${base.totalHeight}px">${tiles}</div>`;
  items.slice(startIndex, endIndex).forEach((item) => {
    if (['photo', 'video', 'document'].includes(item.type) && !state.thumbLoaded.has(item.id)) {
      requestThumb(item.id);
    }
  });
  host.querySelectorAll('img[data-mid]').forEach((img) => {
    const finish = () => {
      if (img.naturalWidth > 0) {
        img.classList.add('loaded');
      } else {
        img.style.display = 'none';
        requestThumb(Number(img.dataset.mid));
      }
    };
    img.addEventListener('load', () => img.classList.add('loaded'));
    img.addEventListener('error', finish);
    if (img.complete) finish();
  });
}

function requestThumb(mid) {
  if (!state.current || state.thumbRequested.has(mid)) return;
  state.thumbRequested.add(mid);
  api('/api/thumb', {
    method: 'POST',
    body: { dialog_id: state.current.id, mid },
  }).catch(() => {});
}

function thumbUrl(mid, dialogId = state.current ? state.current.id : 0) {
  const version = state.thumbLoaded.has(mid) ? 1 : 0;
  return `/api/thumb_image?dialog_id=${dialogId}&mid=${mid}&v=${version}`;
}

function updateThumb(mid) {
  state.thumbLoaded.add(mid);
  $$(`img[data-mid="${mid}"]`).forEach((img) => {
    img.style.display = '';
    img.src = thumbUrl(mid);
  });
  if (state.previewMid === mid && state.previewItem && state.previewItem.type !== 'audio') {
    const image = $('#preview-image');
    image.src = thumbUrl(mid);
    image.hidden = false;
    $('#preview-placeholder').hidden = true;
  }
}
function toggleSelection(mid) {
  const item = state.msgMap.get(mid);
  if (!item || !item.downloadable) return;
  if (state.selected.has(mid)) state.selected.delete(mid);
  else state.selected.add(mid);
  updateRowSelection(mid);
  updateSelection();
}

function updateRowSelection(mid) {
  const row = $(`.msg-row[data-mid="${mid}"]`);
  if (row) {
    const selected = state.selected.has(mid);
    row.classList.toggle('selected', selected);
    const stateEl = row.lastElementChild;
    if (stateEl && stateEl.classList.contains('msg-state') && !stateEl.classList.contains('saved')) {
      stateEl.textContent = selected ? '已选' : '';
      stateEl.classList.toggle('selected-state', selected);
    }
  }
  const tile = $(`.tile[data-mid="${mid}"]`);
  if (tile) tile.classList.toggle('selected', state.selected.has(mid));
}

function selectAllVisible() {
  for (const item of visibleItems()) {
    if (item.downloadable) state.selected.add(item.id);
  }
  renderCurrentView();
  updateSelection();
}

function clearSelection() {
  state.selected.clear();
  renderCurrentView();
  updateSelection();
}

function clearPreview() {
  state.previewMid = null;
  state.previewItem = null;
  state.previewLoading = false;
  resetPreviewMedia();
  $('#preview-name').textContent = '—';
  $('#preview-type').textContent = '—';
  $('#preview-size').textContent = '—';
  $('#preview-date').textContent = '—';
  $('#preview-caption').textContent = '';
  $('#preview-action').textContent = '查看大图';
  $('#preview-action').disabled = true;
  $('#preview-download').disabled = true;
  $$('.msg-row.active, .tile.active').forEach((el) => el.classList.remove('active'));
}

function resetPreviewMedia() {
  const image = $('#preview-image');
  image.hidden = true;
  image.removeAttribute('src');
  const video = $('#preview-video');
  video.pause();
  video.hidden = true;
  video.removeAttribute('src');
  const audio = $('#preview-audio');
  audio.pause();
  audio.hidden = true;
  audio.removeAttribute('src');
  $('#preview-placeholder').hidden = false;
  $('#preview-placeholder').innerHTML = '<span class="preview-glyph">◍</span><p>选择一条消息查看预览</p>';
}

function previewMessage(mid) {
  const item = state.msgMap.get(mid);
  if (!item) return;
  state.previewMid = mid;
  state.previewItem = item;
  state.previewLoading = false;
  resetPreviewMedia();
  $('#preview-name').textContent = item.name || '（无文件名）';
  $('#preview-type').textContent = TYPE_LABELS[item.type] || item.type;
  $('#preview-size').textContent = fmtSize(item.size);
  $('#preview-date').textContent = item.date || '—';
  $('#preview-caption').textContent = item.caption || '';
  $('#preview-download').disabled = !item.downloadable;

  const actions = {
    photo: '查看大图',
    video: '播放视频',
    audio: '播放音频',
    voice: '播放音频',
    document: '打开文件',
  };
  const action = actions[item.type];
  $('#preview-action').textContent = action || '无预览';
  $('#preview-action').disabled = !action;

  if (['photo', 'video', 'document'].includes(item.type)) {
    if (state.thumbLoaded.has(mid)) {
      const image = $('#preview-image');
      image.src = thumbUrl(mid);
      image.hidden = false;
      $('#preview-placeholder').hidden = true;
    } else {
      $('#preview-placeholder').innerHTML = '<span class="preview-glyph">◍</span><p>正在加载缩略图…</p>';
      requestThumb(mid);
    }
  } else if (item.type === 'audio' || item.type === 'voice') {
    $('#preview-placeholder').innerHTML = '<span class="preview-glyph">≈</span><p>音频文件，点击播放预览</p>';
  } else {
    $('#preview-placeholder').innerHTML = '<span class="preview-glyph">◌</span><p>文本消息，无预览</p>';
  }

  $$('.msg-row.active, .tile.active').forEach((el) => el.classList.remove('active'));
  const active = $(`.msg-row[data-mid="${mid}"], .tile[data-mid="${mid}"]`);
  if (active) active.classList.add('active');
}

async function previewAction() {
  if (!state.previewItem || state.previewLoading) return;
  const item = state.previewItem;
  if (!item.downloadable) return;
  const size = Number(item.size || 0);
  if (size > 100 * 1024 * 1024) {
    if (!confirm(`该文件 ${fmtSize(size)}，预览需要先完整下载到临时目录。是否继续？`)) return;
  }
  state.previewLoading = true;
  $('#preview-action').disabled = true;
  $('#preview-action').textContent = '准备预览…';
  try {
    await api('/api/preview', {
      method: 'POST',
      body: { dialog_id: state.current.id, mid: item.id },
    });
  } catch (err) {
    state.previewLoading = false;
    $('#preview-action').disabled = false;
    $('#preview-action').textContent = '重试预览';
    toast(err.message, 'error');
  }
}

function onPreviewProgress(data) {
  if (!state.previewItem || state.previewItem.id !== data.mid) return;
  const pct = data.total ? Math.min(100, Math.round((data.received / data.total) * 100)) : null;
  $('#preview-action').textContent = pct == null ? '准备预览…' : `准备预览 ${pct}%`;
}

function onPreviewReady(data) {
  if (!state.previewItem || state.previewItem.id !== data.mid) return;
  state.previewLoading = false;
  const item = state.previewItem;
  const url = `/api/preview_media?dialog_id=${state.current.id}&mid=${item.id}`;
  const action = { photo: '查看大图', video: '播放视频', audio: '播放音频', voice: '播放音频', document: '打开文件' }[item.type];
  $('#preview-action').disabled = false;
  $('#preview-action').textContent = action || '预览';

  if (data.kind === 'photo') {
    const image = $('#preview-image');
    image.src = url;
    image.hidden = false;
    $('#preview-placeholder').hidden = true;
    $('#lightbox-image').src = url;
    $('#lightbox-caption').textContent = item.name || '';
    $('#lightbox').showModal();
  } else if (data.kind === 'video') {
    const video = $('#preview-video');
    video.src = url;
    video.hidden = false;
    $('#preview-image').hidden = true;
    $('#preview-placeholder').hidden = true;
    video.play().catch(() => {});
  } else if (data.kind === 'audio' || data.kind === 'voice') {
    const audio = $('#preview-audio');
    audio.src = url;
    audio.hidden = false;
    $('#preview-placeholder').hidden = true;
    audio.play().catch(() => {});
  } else {
    window.open(url, '_blank');
  }
  toast('预览已就绪', 'success', 1800);
}

function onPreviewFail(data) {
  if (state.previewItem && state.previewItem.id !== data.mid) return;
  state.previewLoading = false;
  $('#preview-action').disabled = false;
  $('#preview-action').textContent = '重试预览';
  toast(`预览失败：${data.error}`, 'error', 5000);
}
function openFilter() {
  const filter = state.filter;
  $('#f-keyword').value = filter.keyword || '';
  $('#f-date-from').value = filter.date_from || '';
  $('#f-date-to').value = filter.date_to || '';
  $('#f-size-min').value = filter.size_min == null ? '' : filter.size_min / (1024 * 1024);
  $('#f-size-max').value = filter.size_max == null ? '' : filter.size_max / (1024 * 1024);
  $('#f-undownloaded').checked = !!filter.undownloaded;
  $('#filter-dialog').showModal();
}

function applyFilter() {
  const keyword = $('#f-keyword').value.trim();
  const dateFrom = $('#f-date-from').value;
  const dateTo = $('#f-date-to').value;
  if (dateFrom && dateTo && dateFrom > dateTo) {
    toast('开始日期不能晚于结束日期', 'error');
    return;
  }
  const minValue = $('#f-size-min').value.trim();
  const maxValue = $('#f-size-max').value.trim();
  const sizeMin = minValue ? Math.round(Number(minValue) * 1024 * 1024) : null;
  const sizeMax = maxValue ? Math.round(Number(maxValue) * 1024 * 1024) : null;
  if ((sizeMin != null && Number.isNaN(sizeMin)) || (sizeMax != null && Number.isNaN(sizeMax))) {
    toast('大小请填写数字（MB）', 'error');
    return;
  }
  if (sizeMin != null && sizeMax != null && sizeMin > sizeMax) {
    toast('最小大小不能大于最大大小', 'error');
    return;
  }
  state.filter = {
    keyword,
    date_from: dateFrom,
    date_to: dateTo,
    size_min: sizeMin,
    size_max: sizeMax,
    undownloaded: $('#f-undownloaded').checked,
  };
  $('#filter-dialog').close();
  resetListScroll();
  resetGalleryScroll();
  renderCurrentView();
  updateSelection();
  updateFilterButton();
}

function resetFilter() {
  state.filter = { keyword: '', date_from: '', date_to: '', size_min: null, size_max: null, undownloaded: false };
  $('#filter-dialog').close();
  resetListScroll();
  resetGalleryScroll();
  renderCurrentView();
  updateSelection();
  updateFilterButton();
}

function updateFilterButton() {
  const filter = state.filter;
  const active = !!(filter.keyword || filter.date_from || filter.date_to || filter.size_min != null || filter.size_max != null || filter.undownloaded);
  $('#filter-open').style.color = active ? 'var(--accent-strong)' : '';
  $('#filter-open').style.borderColor = active ? 'var(--accent)' : '';
}

function openSettings() {
  const cfg = state.config || {};
  $('#set-api-id').value = cfg.api_id || '';
  $('#set-api-hash').value = '';
  $('#set-api-hash').placeholder = cfg.api_hash_set ? '已保存，留空则不修改' : 'my.telegram.org 获取';
  $('#set-download-dir').value = cfg.download_dir || '';
  $('#set-concurrency').value = String(cfg.concurrency || 8);
  $('#set-page-size').value = String(cfg.page_size || 200);
  $('#set-proxy-enabled').checked = !!cfg.proxy_enabled;
  $('#set-classify-enabled').checked = cfg.classify_enabled !== false;
  $('#set-proxy-type').value = cfg.proxy_type || 'socks5';
  $('#set-proxy-host').value = cfg.proxy_host || '127.0.0.1';
  $('#set-proxy-port').value = String(cfg.proxy_port || 7897);
  $('#settings-dialog').showModal();
}

async function saveSettings() {
  const payload = {
    api_id: $('#set-api-id').value.trim(),
    api_hash: $('#set-api-hash').value.trim(),
    download_dir: $('#set-download-dir').value.trim(),
    concurrency: Number($('#set-concurrency').value || 8),
    page_size: Number($('#set-page-size').value || 200),
    proxy_enabled: $('#set-proxy-enabled').checked,
    proxy_type: $('#set-proxy-type').value,
    proxy_host: $('#set-proxy-host').value.trim(),
    proxy_port: Number($('#set-proxy-port').value || 7897),
    classify_enabled: $('#set-classify-enabled').checked,
  };
  try {
    await api('/api/settings', { method: 'POST', body: payload });
    $('#settings-dialog').close();
    state.authorized = false;
    state.account = '';
    state.dialogs = [];
    state.current = null;
    state.messages = [];
    state.msgMap = new Map();
    state.selected.clear();
    renderChannels();
    renderCurrentView();
    updateAccount();
    showLogin();
    setLoginStatus('正在重新连接…');
    setStatus('设置已保存，正在重新连接');
    toast('设置已保存', 'success');
    state.config.api_id = Number(payload.api_id) || state.config.api_id;
    state.config.api_hash_set = state.config.api_hash_set || !!payload.api_hash;
    state.config.download_dir = payload.download_dir;
    state.config.concurrency = payload.concurrency;
    state.config.page_size = payload.page_size;
    state.config.classify_enabled = payload.classify_enabled;
    $('#concurrency').value = String(payload.concurrency);
    $('#download-dir').value = payload.download_dir;
    state.pageSize = payload.page_size;
  } catch (err) {
    toast(err.message, 'error', 5000);
  }
}

function openSubs() {
  const select = $('#subs-channel');
  if (!state.dialogs.length) {
    select.innerHTML = '<option value="">暂无频道</option>';
  } else {
    select.innerHTML = state.dialogs.map((d) => `<option value="${d.id}">${esc(d.title)}</option>`).join('');
    if (state.current) select.value = String(state.current.id);
  }
  renderSubs();
  $('#subs-dialog').showModal();
}

function renderSubs() {
  const host = $('#subs-list');
  if (!state.subs.length) {
    host.innerHTML = '<p class="rail-empty">还没有追更的频道。选择频道和间隔后添加。</p>';
    return;
  }
  host.innerHTML = state.subs.map((sub) => {
    const last = sub.last_check ? new Date(sub.last_check * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未检查';
    return `<div class="sub-row" data-id="${sub.dialog_id}">
      <span class="sub-title">${esc(sub.title)}</span>
      <span class="sub-meta">${sub.interval_min} 分钟</span>
      <span class="sub-meta">${esc(last)} · ${sub.enabled ? '启用' : '停用'}</span>
      <span class="sub-actions">
        <button class="ghost-button" data-sub-action="toggle">${sub.enabled ? '停用' : '启用'}</button>
        <button class="ghost-button" data-sub-action="check">检查</button>
        <button class="danger-button" data-sub-action="delete">删除</button>
      </span>
    </div>`;
  }).join('');
}

async function addSub() {
  const dialogId = Number($('#subs-channel').value || 0);
  if (!dialogId) {
    toast('请选择频道', 'error');
    return;
  }
  const interval = Number($('#subs-interval').value || 10);
  try {
    const data = await api('/api/subs', {
      method: 'POST',
      body: { action: 'add', dialog_id: dialogId, title: state.dialogs.find((d) => d.id === dialogId)?.title || '', interval_min: interval },
    });
    state.subs = data.subs || state.subs;
    renderSubs();
    toast('已添加追更频道', 'success');
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function subAction(action, dialogId) {
  try {
    const data = await api('/api/subs', {
      method: 'POST',
      body: { action, dialog_id: dialogId },
    });
    state.subs = data.subs || state.subs;
    renderSubs();
    setRight(`${state.subs.length} 个追更`);
    if (action === 'check') toast('正在检查新消息…');
  } catch (err) {
    toast(err.message, 'error');
  }
}
async function startDownload(overrideIds, options = {}) {
  if (!state.current) {
    toast('请先选择频道', 'error');
    return;
  }
  if (state.downloading) {
    toast('已有下载任务正在进行', 'error');
    return;
  }
  const ids = overrideIds || [...state.selected];
  if (!ids.length) {
    toast('请勾选要下载的内容', 'error');
    return;
  }
  if (!options.skipDedupe && ids.length >= 2) {
    const preview = buildDedupePreview(ids);
    if (preview && preview.plan.skip.length) {
      state.dedupePlan = { keep: preview.keep, skip: preview.plan.skip };
      showDedupeDialog(ids, preview);
      return;
    }
  }
  const downloadDir = $('#download-dir').value.trim();
  const concurrency = Math.max(1, Math.min(64, Number($('#concurrency').value || 8)));
  $('#concurrency').value = String(concurrency);

  state.dlState = new Map();
  for (const id of ids) {
    const item = state.msgMap.get(id);
    if (item) state.dlState.set(id, [0, Number(item.size || 0)]);
  }
  state.dlStats = { start: performance.now(), files: 0, bytes: 0 };
  try {
    await api('/api/download', {
      method: 'POST',
      body: {
        dialog_id: state.current.id,
        title: state.current.title,
        ids,
        download_dir: downloadDir,
        concurrency,
      },
    });
    state.downloading = true;
    updateSelection();
    setLive('busy');
    setStatus(`开始下载 ${ids.length} 个文件…`);
    updateProgress();
  } catch (err) {
    toast(err.message, 'error', 5000);
  }
}

async function stopDownload() {
  try {
    await api('/api/stop', { method: 'POST' });
    setStatus('正在停止下载…');
  } catch (err) {
    toast(err.message, 'error');
  }
}

function buildDedupePreview(ids) {
  const dedupe = window.TGDedupe;
  if (!dedupe) return null;
  const items = [];
  for (const id of ids) {
    const item = state.msgMap.get(id);
    if (item) items.push(item);
  }
  if (!items.length) return null;
  const plan = dedupe.planDedupe(items);
  const keepIds = new Set(ids);
  for (const entry of plan.skip) keepIds.delete(entry.id);
  return { plan, keep: [...keepIds], items, unknown: ids.length - items.length, total: ids.length };
}

function showDedupeDialog(ids, preview) {
  const { plan, keep, unknown, total } = preview;
  const savedBytes = plan.skip.reduce((sum, entry) => {
    const item = state.msgMap.get(entry.id);
    return sum + Number((item && item.size) || 0);
  }, 0);
  $('#dedupe-summary').textContent =
    `选中 ${total} 个文件，识别出 ${plan.skip.length} 个重复项，可跳过 ${fmtSize(savedBytes)}；` +
    `去重后保留 ${keep.length} 个${unknown ? `（另有 ${unknown} 个缺少信息，按保留处理）` : ''}。`;
  renderDedupeList(plan.skip);
  $('#dedupe-dialog').showModal();
}

function openDedupe() {
  if (!state.current) {
    toast('请先选择频道', 'error');
    return;
  }
  const ids = [...state.selected];
  if (ids.length < 2) {
    toast('请先勾选至少两个文件再去重', 'info');
    return;
  }
  const preview = buildDedupePreview(ids);
  if (!preview) {
    toast('当前勾选的消息没有可分析的文件信息，请刷新页面后重试', 'error');
    return;
  }
  state.dedupePlan = { keep: preview.keep, skip: preview.plan.skip };
  showDedupeDialog(ids, preview);
}

function renderDedupeList(skips) {
  const list = $('#dedupe-list');
  if (!skips.length) {
    list.innerHTML = '<p class="dedupe-empty">没有发现同名/同区间或旧版本重复，可以放心下载。</p>';
    return;
  }
  const rows = skips.slice(0, 200).map((entry) => `
    <div class="dedupe-row">
      <div class="dedupe-name" title="${esc(entry.name)}">${esc(entry.name || `#${entry.id}`)}</div>
      <div class="dedupe-reason">${esc(entry.reason)}</div>
    </div>
  `).join('');
  const more = skips.length > 200 ? `<p class="dedupe-empty">还有 ${skips.length - 200} 项未展示，同样会被跳过。</p>` : '';
  list.innerHTML = rows + more;
}

function applyDedupe(andDownload) {
  const plan = state.dedupePlan;
  if (!plan) return;
  state.selected = new Set(plan.keep);
  updateSelection();
  renderCurrentView();
  $('#dedupe-dialog').close();
  const skipped = plan.skip.length;
  if (andDownload) {
    startDownload(plan.keep, { skipDedupe: true });
  } else if (skipped) {
    toast(`已取消 ${skipped} 个重复项的勾选`, 'success');
  }
  setStatus(`去重完成：保留 ${plan.keep.length} 个${skipped ? `，跳过 ${skipped} 个` : ''}`);
  state.dedupePlan = null;
}

function downloadAllSelected() {
  $('#dedupe-dialog').close();
  state.dedupePlan = null;
  startDownload([...state.selected], { skipDedupe: true });
}

async function reorganizeFiles() {
  if (!state.current) {
    toast('请先选择频道', 'error');
    return;
  }
  if (state.downloading) {
    toast('请先停止下载，再整理文件', 'error');
    return;
  }
  const ids = state.messages.map((m) => m.id);
  if (!ids.length) {
    toast('当前频道还没有加载消息', 'error');
    return;
  }
  const ok = confirm(
    `将按相册标题/文件名规则重新整理当前频道已下载的文件，并清理 0 字节临时文件。\n只会处理已加载的 ${ids.length} 条消息，可能需要一些时间。是否继续？`
  );
  if (!ok) return;
  setStatus('正在整理文件…');
  try {
    const data = await api('/api/reorganize', {
      method: 'POST',
      body: { dialog_id: state.current.id, ids },
    });
    toast(
      `整理完成：移动 ${data.moved} 个，跳过 ${data.skipped} 个，清理临时文件 ${data.cleaned} 个`,
      'success',
      7000
    );
    setStatus(`整理完成：移动 ${data.moved}，跳过 ${data.skipped}`);
  } catch (err) {
    toast(err.message, 'error', 6000);
    setStatus('整理失败');
  }
}

function updateProgress() {
  if (!state.dlState.size) {
    $('#progress-fill').style.width = '0%';
    $('#progress-label').textContent = '0% · 0/0';
    return;
  }
  let done = 0;
  let total = 0;
  let doneCount = 0;
  for (const [received, size] of state.dlState.values()) {
    if (size) {
      done += Math.min(received, size);
      total += size;
      if (received >= size) doneCount += 1;
    } else {
      done += received ? 1 : 0;
      total += 1;
      if (received > 0) doneCount += 1;
    }
  }
  const pct = total ? Math.min(100, (done / total) * 100) : 0;
  $('#progress-fill').style.width = `${pct}%`;
  let label = `${pct.toFixed(0)}% · ${doneCount}/${state.dlState.size}`;
  if (state.downloading && state.dlStats.start) {
    const secs = (performance.now() - state.dlStats.start) / 1000;
    if (secs >= 2) {
      const mbps = state.dlStats.bytes / 1048576 / secs;
      const fps = state.dlStats.files / secs;
      label += ` · ${mbps.toFixed(2)} MB/s · ${fps.toFixed(1)} 个/s`;
    }
  }
  $('#progress-label').textContent = label;
}

function scheduleProgress() {
  if (progressTimer) return;
  progressTimer = setTimeout(() => {
    progressTimer = null;
    updateProgress();
  }, 150);
}

function scheduleSelection() {
  if (selectionTimer) return;
  selectionTimer = setTimeout(() => {
    selectionTimer = null;
    updateSelection();
  }, 200);
}

function onDownloadStarted(data) {
  state.downloading = true;
  setLive('busy');
  updateSelection();
  setStatus(`开始下载：${data.title || ''} · ${data.count || 0} 个文件`);
}

function onDlProgress(data) {
  const current = state.dlState.get(data.mid);
  if (current) {
    state.dlState.set(data.mid, [data.received || 0, data.total || current[1] || 0]);
  } else {
    state.dlState.set(data.mid, [data.received || 0, data.total || 0]);
  }
  scheduleProgress();
}

function onDlFileDone(data) {
  const stats = state.dlStats;
  if (stats.start && data.text === '完成') {
    const entry = state.dlState.get(data.mid);
    stats.files += 1;
    stats.bytes += entry ? Number(entry[1] || 0) : 0;
  }
  setStatus(`下载：${data.text || ''}`);
}

function onHistoryUpdate(data) {
  if (!state.current || data.dialog_id !== state.current.id) return;
  state.history.add(data.mid);
  const item = state.msgMap.get(data.mid);
  if (!item) return;
  if (state.filter.undownloaded) {
    renderCurrentView();
    scheduleSelection();
    $('#stat-downloaded').textContent = String(state.history.size);
    return;
  }
  const row = $(`.msg-row[data-mid="${data.mid}"]`);
  if (row) {
    const stateEl = row.lastElementChild;
    if (stateEl && stateEl.classList.contains('msg-state')) {
      stateEl.textContent = '已存';
      stateEl.className = 'msg-state saved';
    }
  }
  const tile = $(`.tile[data-mid="${data.mid}"]`);
  if (tile && !tile.querySelector('.tile-saved')) {
    const badge = document.createElement('span');
    badge.className = 'tile-saved';
    badge.textContent = '已存';
    tile.appendChild(badge);
  }
  scheduleSelection();
  $('#stat-downloaded').textContent = String(state.history.size);
}

function onDlDone(stats) {
  state.downloading = false;
  setLive('on');
  updateSelection();
  const parts = [`完成 ${stats.done || 0}`, `跳过 ${stats.skipped || 0}`, `失败 ${stats.failed || 0}`];
  if (stats.stopped) parts.push(`停止 ${stats.stopped}`);
  setStatus(`下载结束：${parts.join('，')}`);
  toast(`下载结束：${parts.join('，')}`, stats.failed ? 'error' : 'success', 5000);
  if ((stats.done || 0) + (stats.skipped || 0)) {
    $('#progress-fill').style.width = '100%';
    const secs = state.dlStats.start ? (performance.now() - state.dlStats.start) / 1000 : 0;
    const mbps = secs > 0 ? state.dlStats.bytes / 1048576 / secs : 0;
    $('#progress-label').textContent = secs > 0
      ? `100% · 平均 ${mbps.toFixed(2)} MB/s`
      : '100%';
  }
}

function connectEvents() {
  const source = new EventSource('/api/events');
  source.onopen = () => setLive(state.downloading ? 'busy' : 'on');
  source.onerror = () => setLive('off');
  source.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    const handler = handlers[payload.type];
    if (handler) handler(payload.data || {});
  };
}

const handlers = {
  login_qr: (data) => {
    showLogin();
    showQR(data);
  },
  login_status: (data) => setLoginStatus(data.text || ''),
  login_ok: (data) => {
    state.authorized = true;
    state.account = data.account || '';
    hideLogin();
    updateAccount();
    setStatus(`已登录：${state.account}`);
    toast(`已登录：${state.account}`, 'success');
  },
  need_password: () => {
    state.passwordSubmitted = false;
    $('#password-input').value = '';
    $('#password-dialog').showModal();
    setTimeout(() => $('#password-input').focus(), 60);
  },
  dialogs: (data) => {
    state.dialogs = data.items || [];
    renderChannels();
  },
  comment_dialog: (data) => {
    const existing = state.dialogs.find((d) => d.id === data.id);
    if (existing) {
      Object.assign(existing, data);
    } else {
      const index = state.dialogs.findIndex((d) => d.id === data.parent_id);
      if (index >= 0) state.dialogs.splice(index + 1, 0, data);
      else state.dialogs.push(data);
    }
    renderChannels();
    selectChannel(data.id);
    toast('已打开评论区', 'success', 4000);
  },
  comment_fail: (data) => {
    toast(data.error || '无法打开评论区', 'error', 6000);
    setStatus('无法打开评论区');
  },
  messages: onMessages,
  thumb: (data) => updateThumb(data.mid),
  thumb_fail: () => {},
  preview_progress: onPreviewProgress,
  preview_ready: onPreviewReady,
  preview_fail: onPreviewFail,
  dl_progress: onDlProgress,
  dl_file_done: onDlFileDone,
  history_update: onHistoryUpdate,
  dl_done: onDlDone,
  download_started: onDownloadStarted,
  auto_new: (data) => {
    if (data.deferred) {
      toast(`追更：${data.title} 有新内容，当前下载结束后继续`, 'info', 5000);
    } else if ((data.items || []).length) {
      toast(`追更：${data.title} 新增 ${data.items.length} 个文件`, 'success', 4200);
    }
  },
  subs: (data) => {
    state.subs = data.subs || [];
    if ($('#subs-dialog').open) renderSubs();
    setRight(`${state.subs.length} 个追更`);
  },
  log: (data) => setStatus(data.text || ''),
  status: (data) => setStatus(data.text || ''),
};

async function submitPassword() {
  const password = $('#password-input').value;
  if (!password) {
    toast('请输入密码', 'error');
    return;
  }
  state.passwordSubmitted = true;
  try {
    await api('/api/password', { method: 'POST', body: { password } });
    $('#password-dialog').close();
    setLoginStatus('正在验证…');
  } catch (err) {
    state.passwordSubmitted = false;
    toast(err.message, 'error');
  }
}
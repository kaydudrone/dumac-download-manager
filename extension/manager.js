// Dumac Download Manager window: reads the helper's history once a second and drives it with toolbar,
// right-click menu and dialogs. The same page runs in the Brave extension and in Download Manager.app.
const call = (msg) => new Promise((resolve) => chrome.runtime.sendNativeMessage('io.github.kaydudrone.dumac', msg, (res) =>
  resolve(chrome.runtime.lastError ? { error: 'Helper not installed. Run install.sh again.' } : res)));

const $ = (id) => document.getElementById(id);
let items = [];
let queueRunning = false;
let settings = null;
let cat = 'all';
let sort = { key: null, dir: 1 };   // null = newest first
let statusId = null;                // download shown in the status window
let statusOpenedAt = 0;             // a just-started download may not be in the list for a moment
let power = null;                   // pending sleep / shut down after the queue
const selected = new Set();

// ---- formatting -------------------------------------------------------------

const EXT = {
  video: ['mp4', 'mkv', 'webm', 'mov', 'avi', 'm4v', 'flv'],
  music: ['mp3', 'm4a', 'wav', 'aac', 'flac', 'ogg', 'opus'],
  compressed: ['zip', 'rar', '7z', 'gz', 'tar', 'xz'],
  documents: ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'htm', 'html'],
  programs: ['dmg', 'pkg', 'exe', 'msi', 'app', 'apk'],
};

function extOf(it) {
  const m = (it.path || '').match(/\.([a-z0-9]{2,5})$/i);
  if (m) return m[1].toLowerCase();
  if (it.label === 'MP3') return 'mp3';
  if (it.label === 'Playlist') return 'list';
  const u = (it.url || '').split(/[?#]/)[0].match(/\.([a-z0-9]{2,5})$/i); // direct file links
  return u && !/^(html?|php|aspx?)$/i.test(u[1]) ? u[1].toLowerCase() : 'mp4';
}

function categoryOf(it) {
  const ext = extOf(it);
  if (ext === 'list') return 'video';
  return Object.keys(EXT).find((c) => EXT[c].includes(ext)) || 'other';
}

function fmtBytes(b) {
  if (!b) return '';
  const u = ['Bytes', 'KB', 'MB', 'GB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i ? 2 : 0)} ${u[i]}`;
}

function fmtEta(s) {
  if (s == null || !isFinite(s)) return '';
  s = Math.round(s);
  if (s < 60) return `${s} sec`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} sec`;
  return `${Math.floor(s / 3600)} hr ${Math.floor((s % 3600) / 60)} min`;
}

const fmtRate = (bps) => (bps ? `${fmtBytes(bps)}/sec` : '');
const fmtDate = (t) => (t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '');
const nameOf = (it) => it.title || (it.path || '').split('/').pop() || it.url;
const isDone = (it) => it.status === 'done' && it.exists;
const running = (it) => it.status === 'downloading' || it.status === 'starting';

function pct(it) {
  const p = it.progress;
  return p && p.total ? Math.min(100, (100 * (p.done || 0)) / p.total) : null;
}

function siteOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
}

function statusText(it) {
  const p = pct(it);
  const part = it.progress?.count > 1 ? `(${it.progress.index}/${it.progress.count}) ` : '';
  switch (it.status) {
    case 'done': return it.exists ? 'Complete' : 'Moved/deleted';
    case 'failed': return 'Error';
    case 'queued': return 'Queued';
    case 'stopped': return p == null ? 'Stopped' : `Stopped ${p.toFixed(2)}%`;
    default:
      if (it.progress?.phase === 'merging') return `${part}Merging…`;
      if (it.progress?.phase === 'retrying') return `Retrying (${it.progress.attempt}/4)…`;
      return p == null ? 'Connecting…' : `${part}${p.toFixed(2)}%`;
  }
}

const describe = (it) => (it.status === 'failed' && it.error ? it.error : [it.label, siteOf(it.url)].filter(Boolean).join(' · '));

// ---- list -------------------------------------------------------------------

function inCat(it, c) {
  switch (c) {
    case 'all': return true;
    case 'unfinished': return it.status !== 'done';
    case 'finished': return it.status === 'done';
    case 'queues': case 'mainqueue': return !!it.queued;
    default: return categoryOf(it) === c;
  }
}

const STATUS_ORDER = { downloading: 0, starting: 0, queued: 1, stopped: 2, failed: 3, done: 4 };
const SORTERS = {
  name: (it) => nameOf(it).toLowerCase(),
  queued: (it) => (it.queued ? 0 : 1),
  size: (it) => (it.status === 'done' ? it.size : it.progress?.total) || 0,
  status: (it) => STATUS_ORDER[it.status] ?? 9,
  eta: (it) => (running(it) ? it.progress?.eta ?? 1e12 : 1e12),
  rate: (it) => (running(it) ? it.progress?.speed || 0 : 0),
  date: (it) => it.last_try || it.started || 0,
  desc: (it) => describe(it).toLowerCase(),
};

function visibleItems() {
  const list = items.filter((it) => inCat(it, cat));
  if (sort.key) {
    const f = SORTERS[sort.key];
    list.sort((a, b) => (f(a) > f(b) ? 1 : f(a) < f(b) ? -1 : 0) * sort.dir);
  }
  return list;
}

function td(tr, content, cls, tip) {
  const cell = document.createElement('td');
  if (typeof content === 'string') cell.textContent = content; else if (content) cell.append(content);
  cell.title = tip ?? (typeof content === 'string' ? content : '');
  if (cls) cell.className = cls;
  tr.append(cell);
}

function nameCell(it) {
  const wrap = document.createElement('div');
  wrap.className = 'name';
  const badge = document.createElement('span');
  badge.className = `badge ${categoryOf(it)}`;
  badge.textContent = extOf(it) === 'list' ? 'LIST' : extOf(it).toUpperCase().slice(0, 4);
  const text = document.createElement('span');
  text.textContent = nameOf(it);
  wrap.title = nameOf(it);
  wrap.append(badge, text);
  return wrap;
}

function render() {
  for (const li of document.querySelectorAll('#cats li')) li.classList.toggle('sel', li.dataset.cat === cat);
  for (const th of document.querySelectorAll('thead th')) {
    th.classList.toggle('asc', sort.key === th.dataset.sort && sort.dir === 1);
    th.classList.toggle('desc', sort.key === th.dataset.sort && sort.dir === -1);
  }
  const visible = visibleItems();
  for (const id of [...selected]) if (!visible.some((it) => it.id === id)) selected.delete(id);

  const rows = $('rows');
  rows.replaceChildren();
  for (const it of visible) {
    const tr = document.createElement('tr');
    tr.dataset.id = it.id;
    if (selected.has(it.id)) tr.className = 'sel';
    const live = running(it);
    td(tr, nameCell(it));
    const q = document.createElement('span');
    if (it.queued) q.className = 'qmark';
    td(tr, q, '', it.queued ? 'In the main download queue' : '');
    td(tr, fmtBytes(it.status === 'done' ? it.size : it.progress?.total));
    td(tr, statusText(it), it.status === 'failed' || (it.status === 'done' && !it.exists) ? 'failed' : '');
    td(tr, live ? fmtEta(it.progress?.eta) : '');
    td(tr, live ? fmtRate(it.progress?.speed) : '');
    td(tr, fmtDate(it.last_try || it.started));
    td(tr, describe(it), it.status === 'failed' ? 'failed' : '');
    rows.append(tr);
  }
  const empty = $('empty');
  empty.hidden = visible.length > 0;
  if (!visible.length && !empty.dataset.error) {
    empty.textContent = items.length ? 'Nothing in this category.' : 'No downloads yet. Click Add URL, or use "Download this video" on YouTube.';
  }
  updateToolbar();
  updateStatusWindow();
}

const selItems = () => items.filter((it) => selected.has(it.id));
const can = {
  any: () => selected.size > 0,
  running: () => selItems().some(running),
  resumable: () => selItems().some((it) => ['stopped', 'failed', 'queued'].includes(it.status)),
  one: () => selected.size === 1,
  done: () => selected.size === 1 && isDone(selItems()[0]),
  queueAdd: () => selItems().some((it) => !it.queued && it.status !== 'done'),
  queueRemove: () => selItems().some((it) => it.queued),
};

function updateToolbar() {
  for (const b of document.querySelectorAll('[data-need]')) b.disabled = !can[b.dataset.need]();
  $('stopall').disabled = !items.some(running);
  $('delcompleted').disabled = !items.some((it) => it.status === 'done');
  $('startqueue').disabled = queueRunning || !items.some((it) => it.queued && ['queued', 'stopped'].includes(it.status));
  $('stopqueue').disabled = !queueRunning;
}

let refreshing = false;
async function refresh() {
  if (refreshing) return; // a slow reply must not pile up requests
  refreshing = true;
  try {
    const res = await call({ type: 'history' });
    const empty = $('empty');
    if (res.error) {
      items = [];
      empty.dataset.error = '1';
      empty.textContent = res.error;
    } else {
      delete empty.dataset.error;
      items = res.items;
      queueRunning = res.queueRunning;
      power = res.power;
    }
    render();
    renderPower();
  } finally {
    refreshing = false;
  }
}

// ---- dialogs ----------------------------------------------------------------

function message(text, title = 'Dumac Download Manager') {
  $('msgtitle').textContent = title;
  $('msgtext').textContent = text;
  $('msgdlg').showModal();
}

function confirmBox(text) {
  return new Promise((resolve) => {
    const dlg = $('confirmdlg');
    $('confirmtext').textContent = text;
    dlg.returnValue = '';
    dlg.addEventListener('close', () => resolve(dlg.returnValue === 'ok'), { once: true });
    dlg.showModal();
  });
}

async function act(msg) {
  const res = await call(msg);
  if (res.error) message(res.error);
  refresh();
  return res;
}

async function loadSettings() {
  const s = await call({ type: 'settings_get' });
  if (!s.error) settings = s;
  return settings;
}

// Download status window (per-download progress dialog).
function openStatus(id) {
  statusId = String(id);
  statusOpenedAt = Date.now();
  if (!$('statusdlg').open) $('statusdlg').show();
  updateStatusWindow(); // after show(): it skips closed windows
}

function updateStatusWindow() {
  const dlg = $('statusdlg');
  if (!dlg.open || !statusId) return;
  const it = items.find((x) => x.id === statusId);
  if (!it) {
    if (Date.now() - statusOpenedAt > 10000) dlg.close();
    else { $('st-title').textContent = 'Starting download…'; $('st-status').textContent = 'Connecting…'; }
    return;
  }
  const p = pct(it);
  const prog = it.progress || {};
  const done = it.status === 'done';
  $('st-title').textContent = done ? `Download complete — ${nameOf(it)}` : `${p == null ? '' : Math.floor(p) + '% '}${nameOf(it)}`;
  $('st-url').textContent = it.url;
  $('st-status').textContent = {
    done: it.exists ? 'Download complete' : 'Complete (file moved or deleted)',
    failed: `Error: ${it.error || 'download failed'}`,
    stopped: 'Stopped',
    queued: 'Waiting in the main download queue',
  }[it.status] || (prog.phase === 'merging' ? 'Merging video and audio…'
    : prog.phase === 'retrying' ? `Connection lost, retrying (attempt ${prog.attempt} of 4)…`
    : p == null ? 'Connecting…' : 'Receiving data…');
  $('st-cn').textContent = running(it) && prog.connections ? String(prog.connections) : '';
  const total = done ? it.size : prog.total;
  $('st-size').textContent = fmtBytes(total) || 'Unknown';
  $('st-done').textContent = done ? fmtBytes(it.size) + ' (100.00 %)' : p == null ? '' : `${fmtBytes(prog.done)} (${p.toFixed(2)} %)`;
  $('st-rate').textContent = running(it) ? fmtRate(prog.speed) : '';
  $('st-eta').textContent = running(it) ? fmtEta(prog.eta) : '';
  $('st-bar').style.width = `${done ? 100 : p || 0}%`;
  $('st-bar').parentElement.classList.toggle('done', done);
  $('st-note').textContent = done && it.path ? `Saved to ${it.path}` : '';
  $('st-open').hidden = $('st-folder').hidden = !isDone(it);
  $('st-pause').hidden = done;
  $('st-pause').textContent = running(it) ? 'Pause' : 'Resume';
  $('st-cancel').hidden = done || !running(it);
}

$('st-pause').addEventListener('click', () => {
  const it = items.find((x) => x.id === statusId);
  if (it) act({ type: running(it) ? 'stop' : 'resume', ids: [it.id] });
});
$('st-cancel').addEventListener('click', () => { act({ type: 'stop', ids: [statusId] }); $('statusdlg').close(); });
$('st-close').addEventListener('click', () => $('statusdlg').close());
$('st-open').addEventListener('click', () => act({ type: 'open', id: statusId }));
$('st-folder').addEventListener('click', () => act({ type: 'reveal', id: statusId }));

function showProperties(it) {
  const rows = [
    ['File name', nameOf(it)], ['Address', it.url], ['Saved to', it.path || it.dir || ''],
    ['Size', fmtBytes(it.status === 'done' ? it.size : it.progress?.total)], ['Quality', it.label || ''],
    ['Status', statusText(it)], ['Added', fmtDate(it.started)], ['Last try', fmtDate(it.last_try)],
    ['Finished', it.status === 'done' ? fmtDate(it.ended) : ''], ['Queue', it.queued ? 'Main download queue' : ''],
    ['Error', it.status === 'failed' ? it.error || '' : ''],
  ].filter(([, v]) => v);
  const table = $('proptable');
  table.replaceChildren();
  for (const [k, v] of rows) {
    const tr = document.createElement('tr');
    const th = document.createElement('th'); th.textContent = k;
    const tdv = document.createElement('td'); tdv.textContent = v;
    tr.append(th, tdv);
    table.append(tr);
  }
  $('propdlg').showModal();
}

// ---- actions (toolbar, menus, keyboard, native app menu) -----------------------

const ids = () => [...selected];

const kd = {
  // prefill: a link from Safari's "Download this video" bookmark (string); toolbar clicks pass an Event instead.
  async add(prefill) {
    await loadSettings();
    $('url').value = '';
    $('adderr').hidden = true;
    $('addq').value = settings?.quality || 'compatible';
    $('addfolder').textContent = settings?.download_dir || '~/Downloads';
    $('addfolder').title = $('addfolder').textContent;
    if (typeof prefill === 'string' && /^https?:\/\/\S+$/.test(prefill)) {
      $('url').value = prefill;
    } else {
      try { // pre-fill from the clipboard when it holds a link
        const clip = (await navigator.clipboard.readText()).trim();
        if (/^https?:\/\/\S+$/.test(clip)) $('url').value = clip;
      } catch { /* clipboard not available: leave empty */ }
    }
    for (const d of document.querySelectorAll('dialog[open]')) if (d.id !== 'statusdlg') d.close();
    $('adddlg').showModal();
    $('url').select();
  },
  resume: () => act({ type: 'resume', ids: ids() }),
  stop: () => act({ type: 'stop', ids: ids() }),
  stopAll: () => act({ type: 'stop_all' }),
  async del() {
    const n = selected.size;
    if (n && await confirmBox(`Remove ${n} download${n > 1 ? 's' : ''} from the list?`)) {
      await act({ type: 'remove', ids: ids() });
      selected.clear();
      refresh();
    }
  },
  async delCompleted() {
    if (await confirmBox('Remove all completed downloads from the list?')) act({ type: 'remove_completed' });
  },
  open: () => can.done() && act({ type: 'open', id: ids()[0] }),
  reveal: () => can.done() && act({ type: 'reveal', id: ids()[0] }),
  folder: () => act({ type: 'open_folder' }),
  properties: () => can.one() && showProperties(selItems()[0]),
  status: () => can.one() && openStatus(ids()[0]),
  queueAdd: () => act({ type: 'queue_add', ids: ids() }),
  queueRemove: () => act({ type: 'queue_remove', ids: ids() }),
  startQueue: () => act({ type: 'start_queue' }),
  stopQueue: () => act({ type: 'stop_queue' }),
  async options() {
    await loadSettings();
    if (!settings) return message('Could not read Options from the helper.');
    $('optfolder').textContent = settings.download_dir;
    $('optfolder').title = settings.download_dir;
    $('optq').value = settings.quality;
    $('optmax').value = String(settings.queue_max);
    $('optnotify').checked = settings.notify;
    $('optcn').value = String(settings.connections);
    $('optlimit').value = String(settings.speed_limit || 0);
    if (!$('optlimit').value) $('optlimit').value = '0';
    $('optproxy').value = settings.proxy || '';
    $('optsort').checked = settings.sort_folders;
    $('optcatch').checked = settings.catch_browser;
    $('optclip').checked = settings.watch_clipboard;
    $('optlogin').checked = settings.use_browser_login;
    $('optyt').checked = !!settings.youtube_login;
    $('optbrowser').value = settings.login_browser || 'brave';
    $('opterr').hidden = true;
    $('optdlg').showModal();
  },
  async scheduler() {
    await loadSettings();
    if (!settings) return message('Could not read the Scheduler from the helper.');
    const sch = settings.schedule;
    $('schon').checked = sch.enabled;
    $('schtime').value = sch.time;
    for (const box of document.querySelectorAll('#schdays input')) box.checked = sch.days.includes(Number(box.value));
    $('schafter').value = settings.after_queue || 'nothing';
    $('scherr').hidden = true;
    $('schdlg').showModal();
  },
  toggleCats() {
    const hide = !$('side').hidden;
    $('side').hidden = hide;
    $('showcats').hidden = !hide;
  },
  help: () => message('Add URL: paste a link and click Start Download (now) or Download Later (into the queue). '
    + 'Double-click a finished download to open it, or an unfinished one to see its progress. Right-click a row for more. '
    + 'Downloads from the "Download this video" button, the Safari bookmark and your browser show up here too.', 'How to use'),
};
kd.openStatus = (id) => openStatus(id);  // the app opens this for downloads caught from Brave
window.kd = kd; // the Mac app's menu bar calls these

function renderPower() {
  const bar = $('powerbar');
  if (!power) { bar.hidden = true; return; }
  const left = Math.max(0, Math.round(power.at - Date.now() / 1000));
  $('powertext').textContent = `The download queue finished. Your Mac will ${power.action === 'shutdown' ? 'shut down' : 'go to sleep'} in ${left} seconds.`;
  bar.hidden = false;
}
$('powercancel').addEventListener('click', async () => { await call({ type: 'power_cancel' }); power = null; renderPower(); });

const TOOLBAR = { add: kd.add, resume: kd.resume, stop: kd.stop, stopall: kd.stopAll, delete: kd.del,
  delcompleted: kd.delCompleted, options: kd.options, scheduler: kd.scheduler, startqueue: kd.startQueue, stopqueue: kd.stopQueue };
for (const [id, fn] of Object.entries(TOOLBAR)) $(id).addEventListener('click', fn);

// Add URL: Start Download / Download Later.
$('adddlg').querySelector('form').addEventListener('submit', async (e) => {
  const choice = e.submitter?.value;
  if (choice !== 'start' && choice !== 'later') return;
  e.preventDefault();
  const res = await call({ type: 'add', url: $('url').value, quality: $('addq').value, later: choice === 'later' });
  if (res.error) { $('adderr').textContent = res.error; $('adderr').hidden = false; return; }
  $('adddlg').close();
  cat = 'all';
  selected.clear();
  selected.add(res.id);
  await refresh();
  if (choice === 'start') openStatus(res.id);
});

// Options.
let pendingFolder = null;
$('browse').addEventListener('click', async () => {
  const res = await call({ type: 'pick_folder' });
  if (res.path) { pendingFolder = res.path; $('optfolder').textContent = res.path; $('optfolder').title = res.path; }
});
$('optdlg').querySelector('form').addEventListener('submit', async (e) => {
  if (e.submitter?.value !== 'ok') { pendingFolder = null; return; }
  e.preventDefault();
  const next = { quality: $('optq').value, queue_max: Number($('optmax').value), notify: $('optnotify').checked,
    connections: Number($('optcn').value), speed_limit: Number($('optlimit').value), proxy: $('optproxy').value.trim(),
    sort_folders: $('optsort').checked, catch_browser: $('optcatch').checked, watch_clipboard: $('optclip').checked,
    use_browser_login: $('optlogin').checked, youtube_login: $('optyt').checked,
    login_browser: $('optbrowser').value };
  if (pendingFolder) next.download_dir = pendingFolder;
  const res = await call({ type: 'settings_set', settings: next });
  if (res.error) { $('opterr').textContent = res.error; $('opterr').hidden = false; return; }
  settings = res;
  pendingFolder = null;
  $('optdlg').close();
});

// Scheduler (runs while this window is open).
const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
for (const [i, d] of DAY_NAMES.entries()) {
  const label = document.createElement('label');
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.value = String(i);
  label.append(box, d);
  $('schdays').append(label);
}
$('schdlg').querySelector('form').addEventListener('submit', async (e) => {
  if (e.submitter?.value !== 'ok') return;
  e.preventDefault();
  const days = [...document.querySelectorAll('#schdays input:checked')].map((b) => Number(b.value));
  const res = await call({ type: 'settings_set', settings: {
    schedule: { enabled: $('schon').checked, time: $('schtime').value, days }, after_queue: $('schafter').value } });
  if (res.error) { $('scherr').textContent = res.error; $('scherr').hidden = false; return; }
  settings = res;
  $('schdlg').close();
});

let lastFired = '';
async function schedulerTick() {
  const sch = settings?.schedule;
  if (!sch?.enabled) return;
  const now = new Date();
  const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  const key = `${now.toDateString()} ${hhmm}`;
  if (hhmm !== sch.time || !sch.days.includes(now.getDay()) || lastFired === key) return;
  lastFired = key;
  const res = await call({ type: 'start_queue' });
  if (!res.error) refresh(); // an empty queue at the scheduled time is not an error worth a popup
}

// Rows: select, multi-select, double-click, right-click menu.
$('rows').addEventListener('click', (e) => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  if (e.metaKey || e.ctrlKey) {
    if (selected.has(tr.dataset.id)) selected.delete(tr.dataset.id); else selected.add(tr.dataset.id);
  } else if (e.shiftKey && selected.size) {
    const order = visibleItems().map((it) => it.id);
    const a = order.indexOf([...selected].pop());
    const b = order.indexOf(tr.dataset.id);
    for (const id of order.slice(Math.min(a, b), Math.max(a, b) + 1)) selected.add(id);
  } else {
    selected.clear();
    selected.add(tr.dataset.id);
  }
  render();
});

$('rows').addEventListener('dblclick', (e) => {
  const it = items.find((x) => x.id === e.target.closest('tr')?.dataset.id);
  if (!it) return;
  if (isDone(it)) act({ type: 'open', id: it.id }); else openStatus(it.id);
});

const CTX = {
  open: [kd.open, can.done], reveal: [kd.reveal, can.done], resume: [kd.resume, can.resumable], stop: [kd.stop, can.running],
  queue_add: [kd.queueAdd, can.queueAdd], queue_remove: [kd.queueRemove, can.queueRemove], delete: [kd.del, can.any],
  properties: [kd.properties, can.one],
};
const ctx = $('ctx');
$('rows').addEventListener('contextmenu', (e) => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  e.preventDefault();
  if (!selected.has(tr.dataset.id)) { selected.clear(); selected.add(tr.dataset.id); render(); }
  for (const li of ctx.querySelectorAll('li[data-act]')) li.classList.toggle('off', !CTX[li.dataset.act][1]());
  ctx.hidden = false;
  ctx.style.left = `${Math.min(e.clientX, innerWidth - ctx.offsetWidth - 4)}px`;
  ctx.style.top = `${Math.min(e.clientY, innerHeight - ctx.offsetHeight - 4)}px`;
});
ctx.addEventListener('click', (e) => {
  const li = e.target.closest('li[data-act]');
  if (!li || li.classList.contains('off')) return;
  ctx.hidden = true;
  CTX[li.dataset.act][0]();
});
const hideCtx = () => { ctx.hidden = true; };
document.addEventListener('click', (e) => { if (!ctx.contains(e.target)) hideCtx(); });
addEventListener('blur', hideCtx);
$('rows').closest('.grid').addEventListener('scroll', hideCtx);

// Categories tree.
$('cats').addEventListener('click', (e) => {
  const tw = e.target.closest('[data-toggle]');
  if (tw) {
    const parent = tw.dataset.toggle;
    const kids = document.querySelectorAll(`#cats li[data-parent="${parent}"]`);
    const collapse = tw.textContent === '−';
    tw.textContent = collapse ? '+' : '−';
    for (const li of kids) li.classList.toggle('collapsed', collapse);
    if (collapse && [...kids].some((li) => li.dataset.cat === cat)) cat = parent;
    render();
    return;
  }
  const li = e.target.closest('li');
  if (li) { cat = li.dataset.cat; selected.clear(); render(); }
});
$('hidecats').addEventListener('click', kd.toggleCats);
$('showcats').addEventListener('click', kd.toggleCats);

// Column headers sort; a third click goes back to newest first.
document.querySelector('thead').addEventListener('click', (e) => {
  const key = e.target.closest('th')?.dataset.sort;
  if (!key) return;
  if (sort.key !== key) sort = { key, dir: 1 };
  else if (sort.dir === 1) sort.dir = -1;
  else sort = { key: null, dir: 1 };
  render();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') hideCtx();
  if (document.querySelector('dialog[open]:modal')) return;
  if (e.target.closest?.('input, select, textarea')) return;
  if (e.key === 'Backspace' || e.key === 'Delete') { e.preventDefault(); kd.del(); }
  if (e.key === 'Enter') {
    const it = selItems()[0];
    if (selected.size === 1 && it) { if (isDone(it)) kd.open(); else openStatus(it.id); }
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'a') { e.preventDefault(); for (const it of visibleItems()) selected.add(it.id); render(); }
});

loadSettings();
refresh();
setInterval(refresh, 1000);
setInterval(schedulerTick, 15000);
setInterval(loadSettings, 60000); // pick up Options/Scheduler changes made in the other window

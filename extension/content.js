// "Download this video" panel. YouTube: inside the player (watch pages + Shorts).
// Other video sites: floating over the top-right of the largest video, on single-video pages only.
(() => {
  const cache = {};          // video URL -> formats response
  const hidden = new Set();  // videos where the user clicked X
  const YT = /(^|\.)youtube\.com$/.test(location.hostname);
  // Single-video pages (keep in step with VIDEO_PAGE in host/dumac_host.py). Feeds have many videos, so no button there.
  const SITE_PAGE = new RegExp('^https://(www\\.|m\\.|web\\.)?(instagram\\.com/(reel|reels|p|tv)/[\\w-]+'
    + '|tiktok\\.com/@[\\w.-]+/video/\\d+|facebook\\.com/(watch/?\\?v=\\d+|reel/\\d+|[\\w.]+/videos/)|fb\\.watch/\\w+'
    + '|(x|twitter)\\.com/\\w+/status/\\d+|vimeo\\.com/\\d+|dailymotion\\.com/video/\\w+'
    + '|xiaohongshu\\.com/(explore|discovery/item)/\\w+|bilibili\\.com/video/\\w+)');

  function videoUrl() {
    const u = new URL(location.href);
    if (!YT) {
      const href = location.href.split('#')[0];
      return SITE_PAGE.test(href) ? href : null;
    }
    if (u.pathname === '/watch' && u.searchParams.get('v')) return `https://www.youtube.com/watch?v=${u.searchParams.get('v')}`;
    const m = u.pathname.match(/^\/shorts\/([\w-]+)/);
    return m ? `https://www.youtube.com/shorts/${m[1]}` : null;
  }

  function playlistUrl() {
    if (!YT) return null;
    const list = new URL(location.href).searchParams.get('list');
    // RD… = YouTube Mix, an endless auto-generated list; not a real playlist.
    return list && !list.startsWith('RD') ? `https://www.youtube.com/playlist?list=${list}` : null;
  }

  function activePlayer() {
    // Largest visible player on the page (YouTube: the active Short or the watch player; elsewhere: a <video>).
    let best = null, bestArea = 0;
    for (const p of document.querySelectorAll(YT ? '.html5-video-player' : 'video')) {
      const r = p.getBoundingClientRect();
      if (!YT && (r.width < 160 || r.height < 120)) continue;  // thumbnails, avatars
      const visible = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0)) * r.width;
      if (visible > bestArea) { best = p; bestArea = visible; }
    }
    return best;
  }

  const fmtMB = (b) => (b ? `${b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB' : Math.max(1, Math.round(b / 1e6)) + ' MB'}` : '');

  function row(menu, cls, text, onClick, tip) {
    const el = document.createElement('div');
    el.className = cls;
    el.textContent = text;
    if (tip) el.title = tip;
    if (onClick) el.addEventListener('click', (e) => { e.stopPropagation(); onClick(); });
    menu.append(el);
    return el;
  }

  const sep = (menu) => { const d = document.createElement('div'); d.className = 'kd-dl-sep'; menu.append(d); };

  function ask(msg) {
    return new Promise((resolve) => chrome.runtime.sendMessage(msg, (res) => resolve(res || { error: 'No reply from helper' })));
  }

  function start(root, menu, msg, label) {
    menu.replaceChildren();
    row(menu, 'kd-dl-note', `Starting ${label}…`);
    ask(msg).then((res) => {
      menu.replaceChildren();
      row(menu, 'kd-dl-note', res.error ? res.error : `Downloading ${label} to your Downloads folder. You'll get a notification when it's done.`);
      setTimeout(() => root.classList.remove('kd-open'), 3500);
    });
  }

  function render(root, menu, url, data) {
    menu.replaceChildren();
    if (YT) { // playlists only exist on YouTube
      const list = playlistUrl();
      if (list) row(menu, 'kd-dl-item', 'Download all',
        () => start(root, menu, { type: 'download_all', url: list, label: 'Playlist' }, 'the whole playlist'),
        'Every video in this playlist, best quality, into its own folder');
      else row(menu, 'kd-dl-item kd-off', 'Download all', null, 'Open a video from a playlist to download all of it');
      sep(menu);
    }
    if (data.error) return row(menu, 'kd-dl-note', data.error);
    let n = 0;
    for (const it of data.items) {
      const p = parseInt(it.label, 10);
      const hd = p >= 2160 ? ' 4K' : p >= 1440 ? ' 2K' : p >= 720 ? ' HD' : '';
      const label = `${it.label}${hd}`;
      row(menu, 'kd-dl-item', `${++n}. MP4 file, quality ${label}`,
        () => start(root, menu, { type: 'download', url, height: it.height, title: data.title, label }, label), fmtMB(it.bytes));
    }
    row(menu, 'kd-dl-item', `${++n}. MP3 file, audio only`,
      () => start(root, menu, { type: 'download', url, audio: true, title: data.title, label: 'MP3' }, 'MP3'), fmtMB(data.audioBytes));
  }

  function square(text, tip, onClick) {
    const b = document.createElement('button');
    b.className = 'kd-dl-sq';
    b.textContent = text;
    b.title = tip;
    b.addEventListener('click', onClick);
    return b;
  }

  function build() {
    const root = document.createElement('div');
    root.className = 'kd-dl';
    const bar = document.createElement('div');
    bar.className = 'kd-dl-bar';
    const btn = document.createElement('button');
    btn.className = 'kd-dl-btn';
    btn.textContent = 'Download this video';
    const menu = document.createElement('div');
    menu.className = 'kd-dl-menu';
    const help = square('?', 'Help', () => {
      menu.replaceChildren();
      row(menu, 'kd-dl-note', 'Pick a quality to save the video to your Downloads folder. MP4 plays in QuickTime and CapCut. X hides this button for this video.');
      root.classList.add('kd-open');
    });
    const close = square('X', 'Hide for this video', () => { hidden.add(videoUrl()); root.remove(); });
    bar.append(btn, help, close);
    root.append(bar, menu);
    // Keep clicks from pausing the video / toggling the player.
    for (const ev of ['click', 'mousedown', 'dblclick']) root.addEventListener(ev, (e) => e.stopPropagation());
    btn.addEventListener('click', async () => {
      if (root.classList.toggle('kd-open') === false) return;
      const url = videoUrl();
      if (!url) return;
      if (!cache[url]) {
        menu.replaceChildren();
        row(menu, 'kd-dl-note', 'Loading qualities…');
        const res = await ask({ type: 'formats', url });
        if (res.error) return render(root, menu, url, res); // don't cache errors
        cache[url] = res;
      }
      render(root, menu, url, cache[url]);
    });
    return root;
  }

  let ui = null;

  let target = null;  // the <video> the floating panel sits on (other sites)

  function place() {
    if (!ui?.parentElement) return;
    if (!YT) {
      if (!target) return;
      const r = target.getBoundingClientRect();
      ui.style.top = `${Math.max(10, r.top + 10)}px`;
      ui.style.left = `${Math.max(10, r.right - ui.offsetWidth - 10)}px`;
      return;
    }
    // Keep the panel below YouTube's fixed top bar when the player scrolls under it.
    const bar = document.querySelector('#masthead-container')?.getBoundingClientRect().bottom || 0;
    ui.style.top = `${Math.max(12, bar - ui.parentElement.getBoundingClientRect().top + 12)}px`;
  }

  // ponytail: 1 s poll instead of observers; YouTube is a SPA and rebuilds players often.
  setInterval(() => {
    const url = videoUrl();
    const player = url && !hidden.has(url) && activePlayer();
    if (!player) { ui?.remove(); return; }
    if (!ui) {
      ui = build();
      if (!YT) ui.classList.add('kd-fixed');
    }
    const host = YT ? player : document.documentElement;
    if (!YT) target = player;
    if (ui.parentElement !== host) { ui.classList.remove('kd-open'); host.append(ui); }
    place();
  }, 1000);
  addEventListener('scroll', place, { passive: true });
  document.addEventListener('click', () => ui?.classList.remove('kd-open'));
})();

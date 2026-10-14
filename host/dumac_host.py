#!/usr/bin/env python3
"""Helper behind Dumac Download Manager (Mac app + browser extension) and the "Download this video" buttons.

Requests are length-prefixed JSON on stdin/stdout (Chrome native messaging); see handle() for the request types.
Downloads run detached (worker mode) so they survive the caller closing the pipe.
Installed by install.sh into ~/Library/Application Support/Dumac Download Manager/ with its own Python venv (yt-dlp).
CLI: `--add URL`, `--worker JOB`, `--power ACTION` (internal).

Two engines:
  video  yt-dlp (YouTube, Instagram, TikTok, … any site yt-dlp has an extractor for)
  file   aria2c with several connections (direct links, downloads caught from the browser)

Files in DATA:
  history.jsonl       JSON lines; records with the same id are merged, later fields win
  progress/<id>.json  live progress of a running download
  cookies/<id>.txt    browser cookies for one download (0600, deleted when it finishes)
  settings.json       Options + Scheduler (the schedule itself is checked by the open Download Manager window)
  queue.json          {"running": bool} for the main download queue
  power.json          pending sleep/shut down after the queue, cancellable for 60 s
"""
import fcntl
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
APP_NAME = "Dumac Download Manager"
APP_ID = "io.github.kaydudrone.dumac"


def _tool(name):
    """Find a Homebrew tool even when launched from the Dock or a browser (no Homebrew on PATH)."""
    return shutil.which(name) or next((p for p in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}")
                                       if os.path.exists(p)), None)


YTDLP = os.path.join(os.path.dirname(sys.executable), "yt-dlp")  # installed in the same venv by install.sh
ARIA2 = _tool("aria2c")
DENO = _tool("deno")  # YouTube needs a JS runtime for its bot checks
FFMPEG_DIR = os.path.dirname(_tool("ffmpeg") or "/opt/homebrew/bin/ffmpeg")
DOWNLOADS = os.path.join(HOME, "Downloads")
DATA = os.path.join(HOME, "Library/Application Support", APP_NAME)
LOG = os.path.join(DATA, "dumac.log")
HISTORY = os.path.join(DATA, "history.jsonl")
PROGRESS_DIR = os.path.join(DATA, "progress")
COOKIE_DIR = os.path.join(DATA, "cookies")
SETTINGS = os.path.join(DATA, "settings.json")
QUEUE_STATE = os.path.join(DATA, "queue.json")
POWER = os.path.join(DATA, "power.json")
LOCK = os.path.join(DATA, ".lock")

# Pages the "Download this video" buttons may send (YouTube + the video sites the extension's button runs on).
VIDEO_PAGE = re.compile(
    r"^https://(www\.|m\.|web\.)?("
    r"youtube\.com/(watch\?v=|shorts/)[\w-]{6,}|youtu\.be/[\w-]{6,}"
    r"|instagram\.com/(reel|reels|p|tv)/[\w-]+"
    r"|tiktok\.com/@[\w.-]+/video/\d+"
    r"|facebook\.com/\S+|fb\.watch/\S+"
    r"|(x|twitter)\.com/\w+/status/\d+"
    r"|vimeo\.com/\d+"
    r"|dailymotion\.com/video/\w+"
    r"|xiaohongshu\.com/(explore|discovery/item)/\w+"
    r"|bilibili\.com/video/\w+"
    r")\S*$")
PLAYLIST = re.compile(r"^https://www\.youtube\.com/playlist\?list=[\w-]+$")
YOUTUBE = re.compile(r"^https://([\w-]+\.)?(youtube\.com|youtu\.be)/")
ANY_URL = re.compile(r"^https?://\S+$")
PROXY = re.compile(r"^(https?|socks5h?)://[\w.-]+(:\d{1,5})?/?$")
# Best quality that plays everywhere (H.264), else AV1, else anything.
BEST_COMPATIBLE = "bv*[vcodec^=avc1]+ba[ext=m4a]/bv*[vcodec^=av01]+ba[ext=m4a]/bv*+ba/b"
# Highest resolution; AV1 first because QuickTime on Apple Silicon plays it (VP9 it doesn't).
BEST_MAX = "bv*[vcodec^=av01]+ba[ext=m4a]/bv*[vcodec^=avc1]+ba[ext=m4a]/bv*+ba/b"
TOTAL_PRINT = "video:KDTOTAL %(filesize,filesize_approx)s %(playlist_index)s %(n_entries)s"
PROGRESS_TEMPLATE = ("download:KDPROG %(progress.downloaded_bytes)s %(progress.total_bytes)s "
                     "%(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s %(progress.status)s")
# aria2c console readout, e.g. "[#2feaab 28MiB/60MiB(47%) CN:8 DL:14MiB ETA:2s]"
ARIA_READOUT = re.compile(r"\[#\w+ ([\d.]+)(B|KiB|MiB|GiB)/([\d.]+)(B|KiB|MiB|GiB)\((\d+)%\)"
                          r"(?: CN:(\d+))?(?: SD:\d+)?(?: DL:([\d.]+)(B|KiB|MiB|GiB))?(?: ETA:(\w+))?\]")
UNIT = {"B": 1, "KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30}
# Worth retrying: the network, not the request. YouTube's rate limit is excluded (retrying makes it worse).
NETWORK_ERROR = re.compile(r"timed? ?out|connection (reset|refused|aborted|closed)|temporary failure|network is unreachable"
                           r"|incompleteread|eof occurred|remote end closed|ssl|handshake|errno 5[0-9]|name resolution", re.I)
NO_RETRY = re.compile(r"429|not a bot|sign in|private video|unavailable|404|403|forbidden", re.I)
RETRY_WAITS = [5, 15, 30]   # seconds before attempts 2, 3, 4

CATEGORY_DIRS = {
    "Video": {"mp4", "mkv", "webm", "mov", "avi", "m4v", "flv"},
    "Music": {"mp3", "m4a", "wav", "aac", "flac", "ogg", "opus"},
    "Compressed": {"zip", "rar", "7z", "gz", "tar", "xz", "tgz", "bz2"},
    "Documents": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "epub", "pages", "numbers", "key"},
    "Programs": {"dmg", "pkg", "exe", "msi", "app", "apk", "iso"},
}

DEFAULT_SETTINGS = {
    "download_dir": DOWNLOADS, "quality": "compatible", "notify": True, "queue_max": 1,
    "connections": 8, "speed_limit": 0, "proxy": "", "sort_folders": False,
    "catch_browser": True, "watch_clipboard": True, "use_browser_login": True, "after_queue": "nothing",
    # Off by default: downloading with a Google login fixes YouTube's "confirm you're not a bot" block,
    # but YouTube can flag accounts that download a lot. login_browser = whose sign-in yt-dlp reads.
    "youtube_login": False, "login_browser": "brave",
    "schedule": {"enabled": False, "time": "02:00", "days": [0, 1, 2, 3, 4, 5, 6]},
}
QUALITY_LABEL = {"compatible": "Best", "max": "Max", "mp3": "MP3"}
LOGIN_BROWSERS = ("brave", "chrome", "edge", "vivaldi", "chromium", "firefox")
POWER_DELAY = 60
POWER_COMMANDS = {
    "sleep": ["pmset", "sleepnow"],
    "shutdown": ["osascript", "-e", 'tell application "System Events" to shut down'],
}


# ---- plumbing ---------------------------------------------------------------

def read_msg():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        sys.exit(0)
    return json.loads(sys.stdin.buffer.read(struct.unpack("=I", raw)[0]))


def send_msg(obj):
    data = json.dumps(obj).encode()
    sys.stdout.buffer.write(struct.pack("=I", len(data)) + data)
    sys.stdout.buffer.flush()


def locked(fn):
    """Run fn while holding the cross-process lock (queue pump, compaction, settings)."""
    os.makedirs(DATA, exist_ok=True)
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        return fn()


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f)
    os.replace(path + ".tmp", path)


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def _remove(path):
    try:
        os.remove(path)
    except (FileNotFoundError, TypeError):
        pass


class _Quiet:  # keeps yt-dlp chatter off stdout (stdout is the message pipe)
    def debug(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


# ---- settings ---------------------------------------------------------------

def load_settings():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    saved = _read_json(SETTINGS, {})
    s.update({k: v for k, v in saved.items() if k in s})
    if not os.path.isdir(s["download_dir"]):
        s["download_dir"] = DOWNLOADS
    return s


def save_settings(new):
    s = load_settings()
    if "download_dir" in new:
        d = os.path.realpath(os.path.expanduser(str(new["download_dir"])))
        if not (d == HOME or d.startswith(HOME + os.sep)) or not os.path.isdir(d):
            return {"error": "Pick a folder inside your home folder"}
        s["download_dir"] = d
    if new.get("quality") in QUALITY_LABEL:
        s["quality"] = new["quality"]
    for key in ("notify", "sort_folders", "catch_browser", "watch_clipboard", "use_browser_login", "youtube_login"):
        if key in new:
            s[key] = bool(new[key])
    if "queue_max" in new:
        s["queue_max"] = max(1, min(5, int(new["queue_max"])))
    if "connections" in new:
        s["connections"] = max(1, min(16, int(new["connections"])))
    if "speed_limit" in new:
        s["speed_limit"] = max(0.0, min(1000.0, float(new["speed_limit"] or 0)))
    if "proxy" in new:
        p = str(new["proxy"] or "").strip()
        if p and not PROXY.match(p):
            return {"error": "Proxy must look like http://host:port or socks5://host:port"}
        s["proxy"] = p
    if new.get("login_browser") in LOGIN_BROWSERS:
        s["login_browser"] = new["login_browser"]
    if new.get("after_queue") in ("nothing", "sleep", "shutdown"):
        s["after_queue"] = new["after_queue"]
    if "schedule" in new:
        sch = new["schedule"]
        t = str(sch.get("time", "02:00"))
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            return {"error": "Time must look like 02:00"}
        days = sorted({int(d) for d in sch.get("days", []) if 0 <= int(d) <= 6})
        if sch.get("enabled") and not days:
            return {"error": "Pick at least one day"}
        s["schedule"] = {"enabled": bool(sch.get("enabled")), "time": t, "days": days}
    _write_json(SETTINGS, s)
    return s


# ---- cookies from the browser -------------------------------------------------

def _cookie_file(entry_id, cookies):
    """Write the browser's cookies for one download as a Netscape cookie file only this user can read."""
    if not cookies:
        return None
    os.makedirs(COOKIE_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(COOKIE_DIR, f"{int(entry_id)}.txt")
    clean = lambda v: re.sub(r"[\t\r\n]", "", str(v))
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies[:400]:
        if not isinstance(c, dict) or not c.get("name") or not c.get("domain"):
            continue
        domain = clean(c["domain"])
        sub = "FALSE" if c.get("hostOnly") else "TRUE"
        if sub == "TRUE" and not domain.startswith("."):
            domain = "." + domain
        lines.append("\t".join([domain, sub, clean(c.get("path") or "/"), "TRUE" if c.get("secure") else "FALSE",
                                str(int(c.get("expirationDate") or 0)), clean(c["name"]), clean(c.get("value", ""))]))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ---- formats (Download this video buttons) -------------------------------------

def _size(f):
    return (f or {}).get("filesize") or (f or {}).get("filesize_approx") or 0


def formats(url, cookies=None):
    from yt_dlp import YoutubeDL
    s = load_settings()
    opts = {"quiet": True, "no_warnings": True, "logger": _Quiet(), "noplaylist": True}
    if DENO:
        opts["js_runtimes"] = {"deno": {"path": DENO}}
    if s["proxy"]:
        opts["proxy"] = s["proxy"]
    tmp_id = str(time.time_ns())
    cookie_path = _cookie_file(tmp_id, cookies)
    if cookie_path:
        opts["cookiefile"] = cookie_path
    elif s["youtube_login"] and YOUTUBE.match(url):
        opts["cookiesfrombrowser"] = (s["login_browser"],)  # the browser's YouTube sign-in (Keychain asks once)
    try:
        with YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
    finally:
        _remove(cookie_path)
    fs = info.get("formats") or []
    audio = max((f for f in fs if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")),
                key=lambda f: f.get("abr") or 0, default=None)
    best = {}  # height -> (label, size)
    for f in fs:
        h, w = f.get("height"), f.get("width")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        label = min(h, w or h)  # vertical videos: 1080x1920 is "1080p"
        best[h] = (label, max(_size(f), best.get(h, (0, 0))[1]))
    items = [{"height": h, "label": f"{lbl}p", "bytes": s + _size(audio)}
             for h, (lbl, s) in sorted(best.items(), reverse=True) if lbl >= 144]
    return {"title": info.get("title", ""), "items": items, "audioBytes": _size(audio)}


def _is_video_site(url):
    """True if yt-dlp has a site-specific extractor for url (otherwise it's a plain file link)."""
    from yt_dlp.extractor import gen_extractor_classes
    return any(ie.ie_key() != "Generic" and ie.suitable(url) for ie in gen_extractor_classes())


# ---- history ----------------------------------------------------------------

def _record(rec):
    os.makedirs(DATA, exist_ok=True)
    with open(HISTORY, "a") as f:  # O_APPEND: small concurrent writes don't interleave
        f.write(json.dumps(rec) + "\n")


def _entries():
    items = {}
    try:
        with open(HISTORY) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue  # a half-written line after a crash
                items.setdefault(r["id"], {}).update(r)
    except FileNotFoundError:
        pass
    return {k: v for k, v in items.items() if not v.get("removed")}


def _compact():
    """Rewrite history as one line per live entry once it grows past 1 MB."""
    def go():
        if os.path.getsize(HISTORY) < 1_000_000:
            return
        entries = sorted(_entries().values(), key=lambda r: int(r["id"]))
        with open(HISTORY + ".tmp", "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in entries)
        os.replace(HISTORY + ".tmp", HISTORY)
    if os.path.exists(HISTORY) and os.path.getsize(HISTORY) >= 1_000_000:
        locked(go)


def _progress_file(entry_id):
    return os.path.join(PROGRESS_DIR, f"{int(entry_id)}.json")


def _drop_progress(entry_id):
    try:
        _remove(_progress_file(entry_id))
    except ValueError:
        pass


def _worker_alive(rec):
    """True if the recorded pid is still our worker for this id (pids get reused)."""
    pid = rec.get("pid")
    if not pid:
        return False
    cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    return "--worker" in cmd and f'"id": "{rec["id"]}"' in cmd


def _sweep_cookies(entries):
    """Delete cookie files of downloads that finished, were removed, or are older than 3 days."""
    if not os.path.isdir(COOKIE_DIR):
        return
    for name in os.listdir(COOKIE_DIR):
        entry_id, path = name.split(".")[0], os.path.join(COOKIE_DIR, name)
        rec = entries.get(entry_id)
        age = time.time() - os.path.getmtime(path)
        # rec None + young = a quality-list lookup still using its file (formats()); leave it.
        if (rec is None and age > 120) or (rec or {}).get("status") == "done" or age > 3 * 86400:
            _remove(path)


def history():
    _compact()
    entries = _entries()
    _sweep_cookies(entries)
    out = sorted(entries.values(), key=lambda r: int(r["id"]), reverse=True)[:500]
    for r in out:
        r.pop("job", None)
        if r.get("status") == "downloading" and not _worker_alive(r) \
                and time.time() - r.get("last_try", r.get("started", 0)) > 10:
            r["status"] = "failed"  # worker died without reporting (crash, reboot)
            r["error"] = r.get("error") or "The download stopped unexpectedly"
        if r.get("status") in ("downloading", "stopped", "failed"):
            p = _read_json(_progress_file(r["id"]), None)
            if p:
                r["progress"] = p
        r["exists"] = bool(r.get("path")) and os.path.exists(r["path"])
    power = _read_json(POWER, None)
    return {"items": out, "queueRunning": _queue_running(),
            "power": power if power and power.get("at", 0) > time.time() - 5 else None}


def _path_of(entry_id):
    """Recorded path for a history id, only if it still exists inside the home folder."""
    path = (_entries().get(str(entry_id)) or {}).get("path") or ""
    real = os.path.realpath(path) if path else ""
    return real if real.startswith(HOME + os.sep) and os.path.exists(real) else None


# ---- downloads --------------------------------------------------------------

def _spawn(job):
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", json.dumps(job)],
                         start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _record({"id": job["id"], "status": "downloading", "pid": p.pid, "last_try": time.time(), "error": ""})


def _safe_filename(name):
    name = os.path.basename(str(name or "").replace("\\", "/")).strip().lstrip(".")
    name = re.sub(r"[\x00-\x1f:]", "", name)[:200]
    return name or None


def start_download(msg, later=False):
    s = load_settings()
    job = {"id": str(time.time_ns()), "url": msg["url"], "dir": s["download_dir"],
           "audio": bool(msg.get("audio")), "playlist": msg.get("type") == "download_all",
           "engine": msg.get("engine", "video")}
    if msg.get("height") is not None:
        job["height"] = int(msg["height"])
    elif msg.get("quality") == "max":
        job["max"] = True
    if msg.get("filename"):
        job["filename"] = _safe_filename(msg["filename"])
    if msg.get("referrer") and ANY_URL.match(str(msg["referrer"])):
        job["referrer"] = str(msg["referrer"])[:2000]
    if msg.get("ua"):
        job["ua"] = re.sub(r"[\r\n]", "", str(msg["ua"]))[:400]
    job["cookies"] = _cookie_file(job["id"], msg.get("cookies"))
    _record({"id": job["id"], "url": job["url"], "started": time.time(), "job": job, "dir": job["dir"],
             "title": str(msg.get("title") or job.get("filename") or "")[:200], "label": str(msg.get("label") or "")[:40],
             "status": "queued" if later else "starting", "queued": later})
    if not later:
        _spawn(job)
    return {"ok": True, "id": job["id"]}


def add_url(msg):
    url = str(msg.get("url", "")).strip()
    if not ANY_URL.match(url):
        return {"error": "Paste a link that starts with http:// or https://"}
    quality = msg.get("quality") if msg.get("quality") in QUALITY_LABEL else load_settings()["quality"]
    video = quality == "mp3" or not ARIA2 or _is_video_site(url)
    return start_download({"type": "download", "url": url, "audio": quality == "mp3", "quality": quality,
                           "engine": "video" if video else "file",
                           "label": QUALITY_LABEL[quality] if video else "File"}, later=bool(msg.get("later")))


def catch_download(msg):
    """A download the browser started (the extension cancelled it there and hands it over here)."""
    url = str(msg.get("url", "")).strip()
    if not ANY_URL.match(url):
        return {"error": "Only http(s) downloads can be taken over"}
    return start_download({"type": "download", "url": url, "engine": "file" if ARIA2 else "video", "label": "File",
                           "filename": msg.get("filename"), "referrer": msg.get("referrer"), "ua": msg.get("ua"),
                           "cookies": msg.get("cookies") if load_settings()["use_browser_login"] else None})


def stop(entry_id):
    rec = _entries().get(str(entry_id))
    if not rec or rec.get("status") != "downloading":
        return
    if _worker_alive(rec):
        try:
            os.killpg(rec["pid"], signal.SIGTERM)  # worker + yt-dlp/aria2c + ffmpeg share the group
        except ProcessLookupError:
            pass
    _record({"id": rec["id"], "status": "stopped", "ended": time.time()})


def resume(entry_id):
    rec = _entries().get(str(entry_id))
    if rec and rec.get("job") and rec.get("status") in ("stopped", "failed", "queued") and not _worker_alive(rec):
        _spawn(rec["job"])  # yt-dlp / aria2c continue from their partial files


# ---- queue + after-queue power action ------------------------------------------

def _queue_running():
    return _read_json(QUEUE_STATE, {}).get("running", False)


def pump():
    """Start waiting queue items up to Options' limit; finish the queue when nothing is left."""
    def go():
        if not _queue_running():
            return
        entries = _entries().values()
        active = [r for r in entries if r.get("queued") and r.get("status") == "downloading" and _worker_alive(r)]
        waiting = sorted((r for r in entries if r.get("queued") and r.get("status") in ("queued", "stopped")),
                         key=lambda r: int(r["id"]))
        for r in waiting[:max(0, load_settings()["queue_max"] - len(active))]:
            _spawn(r["job"])
        if not active and not waiting:
            _write_json(QUEUE_STATE, {"running": False})
            notify("Download queue finished")
            action = load_settings()["after_queue"]
            if action in POWER_COMMANDS:
                subprocess.Popen([sys.executable, os.path.abspath(__file__), "--power", action],
                                 start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    locked(go)


def power_countdown(action, delay=POWER_DELAY):
    """Sleep/shut down the Mac after the queue, unless cancelled (power_cancel deletes POWER) within `delay` s."""
    token = str(time.time_ns())
    _write_json(POWER, {"action": action, "at": time.time() + delay, "token": token})
    words = {"sleep": "go to sleep", "shutdown": "shut down"}[action]
    notify(f"Your Mac will {words} in {delay} seconds. Open {APP_NAME} and click Cancel to stop it.")
    end = time.time() + delay
    while time.time() < end:
        time.sleep(1)
        if _read_json(POWER, {}).get("token") != token:
            return  # cancelled
    _remove(POWER)
    if os.environ.get("KD_POWER_DRYRUN"):
        with open(LOG, "a") as log:
            log.write(f"{time.strftime('%F %T')} POWER DRYRUN {action}\n")
        return
    subprocess.run(POWER_COMMANDS[action])


def start_queue():
    if not any(r.get("queued") and r.get("status") in ("queued", "stopped") for r in _entries().values()):
        return {"error": "No downloads are waiting in the queue. Use Download Later, or right-click → Add to queue."}
    _write_json(QUEUE_STATE, {"running": True})
    pump()
    return {"ok": True}


def stop_queue():
    _write_json(QUEUE_STATE, {"running": False})
    for i, r in _entries().items():
        if r.get("queued") and r.get("status") == "downloading":
            stop(i)
    return {"ok": True}


# ---- worker -----------------------------------------------------------------

def notify(text, title=APP_NAME):
    if not load_settings()["notify"]:
        return
    subprocess.run(["osascript", "-e", "on run argv", "-e",
                    "display notification (item 1 of argv) with title (item 2 of argv)", "-e", "end run",
                    text, title])


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):  # yt-dlp prints NA when unknown
        return None


def _bytes_on_disk(path):
    if os.path.isdir(path):
        return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs)
    return os.path.getsize(path) if os.path.exists(path) else 0


def _eta_seconds(s):
    """aria2c ETA like '1h2m3s' -> seconds."""
    if not s:
        return None
    total = 0
    for value, unit in re.findall(r"(\d+)([hms])", s):
        total += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def _category_dir(job, s):
    """Downloads/<Category> when Options → Sort into category folders is on."""
    base = job.get("dir") or DOWNLOADS
    if not s["sort_folders"]:
        return base
    if job.get("engine") != "file":
        sub = "Music" if job["audio"] else "Video"
    else:
        name = job.get("filename") or job["url"].split("?")[0].rstrip("/").split("/")[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        sub = next((c for c, exts in CATEGORY_DIRS.items() if ext in exts), "")
    path = os.path.join(base, sub) if sub else base
    os.makedirs(path, exist_ok=True)
    return path


def _video_args(job, s, out_dir):
    args = [YTDLP, "-P", out_dir, "--ffmpeg-location", FFMPEG_DIR,
            "--print", TOTAL_PRINT, "--print", "after_move:filepath", "--no-simulate",
            "--progress", "--newline", "--progress-template", PROGRESS_TEMPLATE,
            "--concurrent-fragments", str(s["connections"]), "--retries", "10", "--fragment-retries", "10",
            "--socket-timeout", "30"]
    if DENO:
        args += ["--js-runtimes", f"deno:{DENO}"]
    if s["speed_limit"]:
        args += ["--limit-rate", f"{int(s['speed_limit'] * 1024)}K"]
    if s["proxy"]:
        args += ["--proxy", s["proxy"]]
    if job.get("cookies") and os.path.exists(job["cookies"]):
        args += ["--cookies", job["cookies"]]
    elif s["youtube_login"] and YOUTUBE.match(job["url"]):
        args += ["--cookies-from-browser", s["login_browser"]]  # the browser's YouTube sign-in (Keychain asks once)
    if job.get("referrer"):
        args += ["--referer", job["referrer"]]
    if job.get("playlist"):
        return args + ["--yes-playlist", "-o", "%(playlist_title)s/%(playlist_index)03d %(title)s.%(ext)s",
                       "-f", BEST_COMPATIBLE, "--merge-output-format", "mp4"]
    args.append("--no-playlist")
    if job["audio"]:
        return args + ["-f", "ba/b", "-x", "--audio-format", "mp3"]
    if "height" in job:
        # Exact chosen height; H.264 first (plays everywhere), then AV1, then anything.
        h = job["height"]
        return args + ["-f", f"bv*[height={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height={h}][vcodec^=av01]+ba[ext=m4a]"
                             f"/bv*[height<={h}]+ba/b", "--merge-output-format", "mp4"]
    return args + ["-f", BEST_MAX if job.get("max") else BEST_COMPATIBLE, "--merge-output-format", "mp4"]


def _file_args(job, s, out_dir, connections):
    args = [ARIA2, "-d", out_dir, "-c", "--file-allocation=none", "--summary-interval=1",
            "--console-log-level=notice", "--enable-color=false", "--auto-file-renaming=true",
            "--allow-overwrite=false", "-x", str(connections), "-s", str(connections), "-k", "1M",
            "--max-tries=3", "--retry-wait=3", "--connect-timeout=15", "--timeout=30"]
    if job.get("filename"):
        args += ["-o", job["filename"]]
    if job.get("cookies") and os.path.exists(job["cookies"]):
        args += ["--load-cookies", job["cookies"]]
    if job.get("referrer"):
        args += ["--referer", job["referrer"]]
    if job.get("ua"):
        args += ["--user-agent", job["ua"]]
    if s["speed_limit"]:
        args += ["--max-download-limit", f"{int(s['speed_limit'] * 1024)}K"]
    if s["proxy"].startswith("http"):  # aria2 has no SOCKS support
        args += ["--all-proxy", s["proxy"]]
    return args + [job["url"]]


def _run_once(job, s, out_dir, state, flush, log, connections):
    """One attempt. Returns (ok, path, error, got_bytes)."""
    file_engine = job.get("engine") == "file"
    args = _file_args(job, s, out_dir, connections) if file_engine else _video_args(job, s, out_dir) + ["--", job["url"]]
    base, item_total, path, error, got = 0.0, None, "", "", False
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        line = line.rstrip("\n")
        if file_engine:
            m = ARIA_READOUT.search(line)
            if m:
                done = float(m.group(1)) * UNIT[m.group(2)]
                total = float(m.group(3)) * UNIT[m.group(4)]
                speed = float(m.group(7)) * UNIT[m.group(8)] if m.group(7) else None
                state.update(done=done, total=total or None, speed=speed, eta=_eta_seconds(m.group(9)),
                             phase="downloading", connections=int(m.group(6)) if m.group(6) else None)
                got = got or done > 0
                flush()
                continue
            done_m = re.search(r"Download complete: (.+)$", line)
            if done_m:
                path = done_m.group(1).strip()
            err_m = re.search(r"errorCode=\d+ (.+)$", line) or re.search(r"\[ERROR\] (.+)$", line)
            if err_m:
                error = err_m.group(1).strip()
            if not line.startswith(("FILE:", " ***", "====", "[#")) and line.strip():
                log.write(line + "\n")
        else:
            if line.startswith("KDTOTAL "):
                total, idx, count = (line.split() + [None] * 3)[1:4]
                item_total, base = _num(total), 0.0
                state.update(index=_num(idx), count=_num(count), done=0, total=item_total, phase="downloading")
                flush(True)
            elif line.startswith("KDPROG "):
                done, total, est, speed, eta, status = (line.split() + [None] * 6)[1:7]
                part_done, part_total = _num(done) or 0, _num(total) or _num(est)
                overall_total = item_total or part_total
                state.update(done=min(base + part_done, overall_total or base + part_done), total=overall_total,
                             speed=_num(speed), phase="downloading")
                left = (overall_total or 0) - state["done"]
                state["eta"] = left / state["speed"] if state["speed"] and left > 0 else _num(eta)
                got = got or part_done > 0
                if status == "finished":
                    base += part_total or part_done
                flush(status == "finished")
            elif line.startswith(os.path.realpath(out_dir) + os.sep):
                path = line  # --print after_move:filepath
            else:
                if re.match(r"\[(Merger|ExtractAudio|FixupM3u8|VideoConvertor)\]", line):
                    state.update(phase="merging", speed=None, eta=None)
                    flush(True)
                if line.startswith("ERROR:"):
                    error = line[6:].strip()
                log.write(line + "\n")
    p.wait()
    return p.returncode == 0 and bool(path), path, error, got


def worker(job):
    s = load_settings()
    os.makedirs(PROGRESS_DIR, exist_ok=True)
    out_dir = _category_dir(job, s)
    prog_file = _progress_file(job["id"])
    state = {"done": 0, "total": None, "speed": None, "eta": None, "phase": "connecting",
             "index": None, "count": None, "attempt": 1, "connections": None}
    last_write = [0.0]

    def flush(force=False):
        if force or time.time() - last_write[0] >= 0.8:  # throttle disk writes
            _write_json(prog_file, state)
            last_write[0] = time.time()

    connections = s["connections"]
    attempts = 1 + len(RETRY_WAITS)
    ok, path, error = False, "", ""
    with open(LOG, "a") as log:
        log.write(f"{time.strftime('%F %T')} START {dict(job, cookies=bool(job.get('cookies')))}\n")
        log.flush()
        flush(True)
        for attempt in range(1, attempts + 1):
            state.update(attempt=attempt, phase="connecting" if attempt == 1 else "retrying")
            flush(True)
            ok, path, error, got = _run_once(job, s, out_dir, state, flush, log, connections)
            if ok:
                break
            log.write(f"{time.strftime('%F %T')} attempt {attempt} failed: {error[:200]}\n")
            log.flush()
            if job.get("engine") == "file" and connections > 1 and not got:
                connections = 1  # some servers refuse several connections: fall back to one
            elif NO_RETRY.search(error) or not NETWORK_ERROR.search(error or "network"):
                break
            if attempt < attempts:
                state.update(phase="retrying", speed=None, eta=None)
                flush(True)
                time.sleep(RETRY_WAITS[attempt - 1])
        log.write(f"{time.strftime('%F %T')} {'DONE ' + path if ok else 'FAIL'}\n")

    if ok and job.get("playlist"):
        path = os.path.dirname(path)  # the playlist's folder
    update = {"id": job["id"], "status": "done" if ok else "failed", "ended": time.time()}
    if ok:
        update.update(path=path, size=_bytes_on_disk(path), error="")
        if not (_entries().get(job["id"]) or {}).get("title"):
            # "Title [videoID].mp4" -> "Title"
            update["title"] = re.sub(r" \[[\w-]{6,}\]$", "", os.path.splitext(os.path.basename(path))[0])
        _drop_progress(job["id"])
        _remove(job.get("cookies"))
    else:
        update["error"] = (error or "Download failed")[:300]
    _record(update)
    notify(f"Download complete: {os.path.basename(path)}" if ok else f"Download failed: {update['error'][:80]}")
    pump()


# ---- requests ---------------------------------------------------------------

def handle(msg):
    kind = msg.get("type")
    ids = [str(i) for i in msg.get("ids", [])][:500]
    if kind == "history":
        return history()
    if kind == "settings_get":
        return load_settings()
    if kind == "settings_set":
        return locked(lambda: save_settings(msg.get("settings") or {}))
    if kind == "pick_folder":
        r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Save downloads to:")'],
                           capture_output=True, text=True)
        return {"path": r.stdout.strip().rstrip("/")} if r.returncode == 0 else {"cancelled": True}
    if kind == "stop":
        for i in ids:
            stop(i)
        return {"ok": True}
    if kind == "stop_all":
        _write_json(QUEUE_STATE, {"running": False})
        for i, r in _entries().items():
            if r.get("status") == "downloading":
                stop(i)
        return {"ok": True}
    if kind == "resume":
        for i in ids:
            resume(i)
        return {"ok": True}
    if kind in ("queue_add", "queue_remove"):
        known = _entries()
        for i in ids:
            if i in known:
                _record({"id": i, "queued": kind == "queue_add"})
        return {"ok": True}
    if kind == "start_queue":
        return start_queue()
    if kind == "stop_queue":
        return stop_queue()
    if kind == "power_cancel":
        _remove(POWER)
        return {"ok": True}
    if kind == "remove":  # list only; files stay on disk
        known = _entries()
        for i in ids:
            stop(i)
            _record({"id": i, "removed": True})
            _drop_progress(i)
            _remove((known.get(i, {}).get("job") or {}).get("cookies"))
        return {"ok": True}
    if kind == "remove_completed":
        for i, r in _entries().items():
            if r.get("status") == "done":
                _record({"id": i, "removed": True})
        return {"ok": True}
    if kind in ("open", "reveal"):
        path = _path_of(msg.get("id"))
        if not path:
            return {"error": "File not found (moved or deleted)"}
        subprocess.run(["open", "-R", path] if kind == "reveal" else ["open", path])
        return {"ok": True}
    if kind == "open_folder":
        subprocess.run(["open", load_settings()["download_dir"]])
        return {"ok": True}
    if kind == "open_app":
        r = subprocess.run(["open", "-b", APP_ID], capture_output=True)
        return {"ok": True} if r.returncode == 0 else {"error": f"{APP_NAME} app not installed"}
    if kind == "show_status":
        r = subprocess.run(["open", f"dumac://status?id={int(msg.get('id', 0))}"], capture_output=True)
        return {"ok": True} if r.returncode == 0 else {"error": f"{APP_NAME} app not installed"}
    if kind == "add":
        return add_url(msg)
    if kind == "catch":
        return catch_download(msg)
    url = str(msg.get("url", "")).strip()
    if kind == "download_all":
        return start_download(msg) if PLAYLIST.match(url) else {"error": "Not a YouTube playlist link"}
    if not VIDEO_PAGE.match(url):
        return {"error": "Not a supported video page"}
    s = load_settings()
    allowed = s["youtube_login"] if YOUTUBE.match(url) else s["use_browser_login"]
    cookies = msg.get("cookies") if allowed else None
    if kind == "formats":
        return formats(url, cookies)
    if kind == "download":
        return start_download(dict(msg, cookies=cookies))
    return {"error": "Unknown request"}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(json.loads(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--add":
        # Right-click "Download Video" Quick Action: any site, quality from Options.
        print(json.dumps(add_url({"url": sys.argv[2]})))
    elif len(sys.argv) == 3 and sys.argv[1] == "--power" and sys.argv[2] in POWER_COMMANDS:
        power_countdown(sys.argv[2])
    else:
        try:
            send_msg(handle(read_msg()))
        except Exception as e:  # report instead of dying silently
            send_msg({"error": str(e)[:300]})

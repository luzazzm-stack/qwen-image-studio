"""Qwen Image Studio: local app for Qwen-Image-2.1 running on Kaggle (2x T4).

Serves the web UI on port 8766 (laptop + phone on the same Wi-Fi), holds the Kaggle token, turns the Kaggle
server on/off and keeps the image library in Pictures\\Qwen Image Studio. The JOB QUEUE lives on the Kaggle
gateway (kaggle/qis_server.py), so this app and future phone apps share it; this app only keeps an "outbox"
of jobs made while the server is off, sends them when it is ready, and downloads every finished image.
"""
import asyncio, json, os, random, re, secrets, shutil, socket, subprocess, sys, time, uuid
from concurrent.futures import ThreadPoolExecutor

HERE = os.environ.get("QIS_HOME") or os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("QIS_DATA") or os.path.join(HERE, "data")   # the portable Windows build keeps data in %LOCALAPPDATA%
WEB = os.path.join(HERE, "web")
UPLOADS = os.path.join(DATA, "uploads")
THUMBS = os.path.join(DATA, "thumbs")
for d in (DATA, UPLOADS, THUMBS):
    os.makedirs(d, exist_ok=True)

if not sys.stdout or sys.executable.lower().endswith("pythonw.exe"):
    sys.stdout = sys.stderr = open(os.path.join(DATA, "server.log"), "a", buffering=1, encoding="utf-8")

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

PORT = int(os.environ.get("QIS_PORT", "8766"))
POOL = ThreadPoolExecutor(4)
SIZES = {"1:1": (1024, 1024), "3:4": (896, 1184), "4:3": (1184, 896), "9:16": (768, 1344), "16:9": (1344, 768)}
ACTIVE = ("queued", "submitting", "running")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ the minors safety rule (same code as the Kaggle gateway)
def _load_safety():
    src = open(os.path.join(HERE, "kaggle", "qis_server.py"), encoding="utf-8").read()
    part = src.split("# ---------------------------------------------------------------- safety")[1].split("\n", 1)[1]
    part = part.split("# ----------------------------------------------------------------")[0]
    ns = {"re": re}
    exec(compile(part, "qis_server.safety", "exec"), ns)
    return ns["safety_check"]


safety_check = _load_safety()


# ------------------------------------------------------------------ persistence
def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj):
    p = os.path.join(DATA, name)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


DEFAULT_SETTINGS = {
    "idle_min": 20, "max_hours": 6, "aspect": "1:1", "quality": "turbo", "count": 1, "theme": "dark",
    "pin": f"{random.randint(0, 9999):04d}",
    "output_dir": os.path.join(os.path.expanduser("~"), "Pictures", "Qwen Image Studio"),
    "nsfw": False, "nsfw_strength": 1.0,
    "nsfw_lora_repo": "f23gg/NSFW-LORA-Qwen-Image-2.1", "nsfw_lora_file": "NSFW Qwen Lora.safetensors",
}
settings = {**DEFAULT_SETTINGS, **load("settings.json", {})}
save("settings.json", settings)
os.makedirs(settings["output_dir"], exist_ok=True)

if os.environ.get("QIS_FAKE_KAGGLE") == "1":     # local tests: runs the kernel on this laptop with a fake ComfyUI
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import fake_kaggle as kaggle_ctl  # noqa: E402
else:
    import kaggle_ctl  # noqa: E402  (reads the Kaggle account from data/settings.json)

state = load("state.json", {})
# Persistent key + topic (like Wan Studio's plan for phones): a phone app that knows them can find the server
# (ntfy topic) and talk to it (X-Key) without the laptop.
state.setdefault("topic", "qis-" + secrets.token_hex(8))
state.setdefault("key", kaggle_ctl.new_key())
state.setdefault("power", "off")  # off | starting | ready | stopping | error
save("state.json", state)

outbox = load("outbox.json", [])          # jobs made while the server was off
library = load("library.json", [])
remote = []                               # mirror of the gateway's job list
remote_names = {}                         # local upload name -> name on the Kaggle server (this session)
fetching, fetch_fail, hidden = set(), {}, set()
live = {"stage": "", "pct": 0, "detail": "", "error": None, "gpus": [{}, {}], "uptime": 0, "idle_s": 0, "timings": {},
        "health_fail": 0, "pe": {}, "nsfw": "off", "nsfw_detail": "", "wf_check": None}
quota_cache = {"t": 0, "v": None}
last_user = time.time()


def save_state():
    save("state.json", state)


def save_outbox():
    save("outbox.json", outbox)


def lib_by_id(lid):
    return next((x for x in library if x["id"] == lid), None)


def idle_since():
    return max(last_user, state.get("ready_at", 0), live.get("last_busy", 0))


# ------------------------------------------------------------------ thumbnails (ffmpeg, so the gallery stays light)
FFMPEG = shutil.which("ffmpeg") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "ffmpeg.exe")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def make_thumb(src, dst):
    if os.path.exists(dst) or not os.path.exists(src) or not os.path.exists(FFMPEG):
        return
    try:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", src, "-vf", "scale='min(560,iw)':-2", "-q:v", "4", dst],
                       timeout=60, creationflags=NOWIN)
    except Exception as e:
        log("thumb failed", os.path.basename(src), e)


def make_all_thumbs():
    for x in library:
        make_thumb(os.path.join(settings["output_dir"], x["file"]), os.path.join(THUMBS, x["id"] + ".jpg"))


# ------------------------------------------------------------------ gateway helpers
http: aiohttp.ClientSession = None


def gw(path):
    return state["url"].rstrip("/") + path


async def gw_json(method, path, timeout=30, **kw):
    async with http.request(method, gw(path), headers={"X-Key": state["key"]}, timeout=aiohttp.ClientTimeout(total=timeout), **kw) as r:
        txt = await r.text()
        if r.status >= 400:
            try:
                msg = json.loads(txt).get("error") or txt
            except Exception:
                msg = txt
            raise GatewayError(r.status, str(msg)[:600])
        return json.loads(txt) if txt else None


class GatewayError(RuntimeError):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


async def blocking(fn, *a):
    return await asyncio.get_running_loop().run_in_executor(POOL, fn, *a)


# ------------------------------------------------------------------ power
async def power_on():
    if state["power"] in ("starting", "ready"):
        return
    if state["power"] == "error" and state.get("url"):
        try:
            await gw_json("GET", "/health", timeout=20)
            state.update(power="ready", error=None)
            live.update(health_fail=0)
            save_state()
            log("power on: reconnected to the running server")
            return
        except Exception:
            pass
    # A server may already be running, e.g. started from the phone app (same key + topic): join it, never replace it.
    try:
        url, _ = current_session(await poll_ntfy(time.time() - NTFY_WINDOW))
    except Exception:
        url = None
    if url and await try_attach(url, "power on"):
        return
    state.update(power="starting", url=None, pushed_at=time.time(), error=None, session=uuid.uuid4().hex[:8], ntfy_since=None, origin="pushed")
    live.update(stage="queued", pct=1, detail="Sending the server to Kaggle", error=None, health_fail=0, gpus=[{}, {}],
                pe={}, nsfw="off", wf_check=None)
    remote.clear()
    remote_names.clear()
    save_state()
    try:
        st = await blocking(kaggle_ctl.status)
        if st in ("running", "queued"):
            # Starting elsewhere (phone) and not announced yet: wait for its address instead of killing it.
            state.update(ntfy_since=time.time() - NTFY_WINDOW, origin="joined")
            live.update(detail="Joining the server that is already starting")
            save_state()
            log("power on: a session is already", st, "- joining it instead of pushing a new one")
            return
        await blocking(kaggle_ctl.push, state["key"], state["topic"], settings["idle_min"] + 5, settings["max_hours"],
                       settings.get("nsfw_lora_repo", ""), settings.get("nsfw_lora_file", ""))
        live.update(stage="queued", pct=2, detail="Waiting for Kaggle to give us 2 GPUs")
        quota_cache["t"] = 0
    except Exception as e:
        msg = str(e)
        if "phone" in msg.lower() or "verif" in msg.lower():
            msg = "Kaggle gives GPUs only to accounts with a verified phone number. Verify yours at kaggle.com/settings, then try again. (" + msg[:200] + ")"
        state.update(power="error", error=f"Could not start on Kaggle: {msg}")
        save_state()
        log("power_on failed", e)


async def power_off(reason="Turned off"):
    if state["power"] == "off":
        return
    prev = state["power"]
    state["power"] = "stopping"
    live.update(stage="stopping", detail=reason)
    save_state()
    if state.get("url") and prev in ("ready", "starting"):
        try:
            await sync_once()      # save any finished images first
        except Exception:
            pass
        try:
            await gw_json("POST", "/shutdown", timeout=15)
        except Exception:
            pass
    await asyncio.sleep(3)
    try:
        await blocking(kaggle_ctl.delete)  # guarantees the GPU session is gone
    except Exception as e:
        log("delete failed", e)
    requeue_unfinished("The server was turned off before this finished")
    state.update(power="off", url=None, last_stop_reason=reason, stopped_at=time.time())
    live.update(stage="", pct=0, detail="", gpus=[{}, {}])
    quota_cache["t"] = 0
    save_state()
    log("power off:", reason)


def requeue_unfinished(note):
    """Jobs that were waiting or running on the server go back to the outbox, so they run next time."""
    for j in remote:
        if j["status"] in ACTIVE and j["kind"] != "improve" and j.get("source") == "laptop" and j.get("local"):
            outbox.append({**j["local"], "count": 1, "seed": j["params"].get("seed", -1), "local_id": "o" + uuid.uuid4().hex[:9],
                           "note": note + " - it will run next time."})
    remote.clear()
    save_outbox()


async def laptop_online():
    try:
        await asyncio.get_running_loop().getaddrinfo("www.kaggle.com", 443)
        return True
    except Exception:
        return False


NTFY_WINDOW = 13 * 3600            # ntfy keeps messages ~12 h; sessions last at most 12 h
OFF_POLL = float(os.environ.get("QIS_OFF_POLL", "60"))   # while off: look for a server started elsewhere (phone)


async def poll_ntfy(since=None):
    """The topic's messages (oldest first) sent after `since` (default: this session's start)."""
    if since is None:
        since = state.get("ntfy_since") or state.get("pushed_at", time.time())
    since = int(since - 5)
    if hasattr(kaggle_ctl, "messages"):           # local tests (tools/fake_kaggle.py)
        return [m for m in kaggle_ctl.messages() if m.get("t", 0) >= since]
    async with http.get(f"https://ntfy.sh/{state['topic']}/json?poll=1&since={since}", timeout=aiohttp.ClientTimeout(total=20)) as r:
        txt = await r.text()
    msgs = []
    for line in txt.splitlines():
        try:
            m = json.loads(json.loads(line).get("message") or "{}")
            if m.get("t", 0) >= since:
                msgs.append(m)
        except Exception:
            pass
    return sorted(msgs, key=lambda m: m.get("t", 0))


def current_session(msgs):
    """(url, None) if the newest event announces a server address, else (None, newest stopped/error event or None)."""
    url, last = None, None
    for m in msgs:
        t = m.get("type")
        if t in ("url", "ready") and m.get("url"):
            url, last = m["url"], m
        elif t in ("stopped", "error"):
            url, last = None, m
    return url, (None if url else last)


async def try_attach(url, why):
    """Join the server at `url` if it answers with our key (started from a phone, or by an earlier run of this app)."""
    try:
        async with http.get(url.rstrip("/") + "/health", headers={"X-Key": state["key"]}, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return False
            h = await r.json(content_type=None)
    except Exception:
        return False
    if h.get("error"):
        return False
    state.update(url=url, power="ready" if h.get("ready") else "starting", error=None, ntfy_since=None, origin="joined",
                 pushed_at=time.time() - float(h.get("uptime") or 0) - 30)
    if h.get("ready"):
        state["ready_at"] = time.time()
    live.update(health_fail=0, stage=h.get("stage", ""), pct=h.get("pct", 0), detail=h.get("detail", ""))
    remote.clear()
    remote_names.clear()
    save_state()
    log(f"{why}: joined the running server {url} (ready={h.get('ready')}, up {h.get('uptime')} s)")
    return True


async def watch_while_off():
    """While this app is off, notice a server started from the phone and attach to it (sends waiting jobs,
    downloads the phone's images, runs auto-off)."""
    since = max(time.time() - NTFY_WINDOW, state.get("stopped_at", 0) + 1)
    url, _ = current_session(await poll_ntfy(since))
    if url and state["power"] == "off":
        await try_attach(url, "server started elsewhere")


async def power_loop():
    last_off_check = 0
    while True:
        try:
            if state["power"] == "off" and time.time() - last_off_check >= OFF_POLL:
                last_off_check = time.time()
                await watch_while_off()
            await power_tick()
        except Exception as e:
            log("power_tick error", repr(e))
        await asyncio.sleep(4)


async def power_tick():
    p = state["power"]
    if p not in ("starting", "ready"):
        return
    if not state.get("url") or live["health_fail"] >= 3:
        try:
            msgs = await poll_ntfy()
        except Exception:
            msgs = []
        url, last = current_session(msgs)
        if url and url != state.get("url"):
            state["url"] = url
            live["health_fail"] = 0
            save_state()
        # only events of this session count (the topic is permanent, so older sessions' messages are there too)
        elif last and last.get("t", 0) >= state.get("pushed_at", 0) - 5:
            if last.get("type") == "error":
                state.update(power="error", error="The Kaggle server failed to start: " + last.get("error", ""))
                save_state()
                await blocking(kaggle_ctl.delete)
                return
            if last.get("type") == "stopped":
                requeue_unfinished("The server stopped (" + last.get("reason", "") + ")")
                state.update(power="off", url=None, last_stop_reason="Kaggle server stopped: " + last.get("reason", ""), stopped_at=time.time())
                save_state()
                return
    if not state.get("url"):
        waited = time.time() - state.get("pushed_at", time.time())
        live.update(stage="queued", pct=2, detail=f"Waiting for Kaggle to give us 2 GPUs ({int(waited)} s)")
        if waited > 900:
            st = await blocking(kaggle_ctl.status)
            if st not in ("running", "queued"):
                state.update(power="error", error=f"Kaggle session did not start (status: {st}).")
                save_state()
        return
    try:
        h = await gw_json("GET", "/health", timeout=20)
        live.update(stage=h["stage"], pct=h["pct"], detail=h["detail"], error=h["error"], uptime=h["uptime"], idle_s=h["idle_s"],
                    timings=h.get("timings", {}), pe=h.get("pe", {}), nsfw=h.get("nsfw", "off"), nsfw_detail=h.get("nsfw_detail", ""),
                    wf_check=h.get("wf_check"), gpus=h.get("gpus", [{}, {}]), health_fail=0)
        if h["error"]:
            state.update(power="error", error="The Kaggle server failed to start: " + h["error"][:600])
            save_state()
            await blocking(kaggle_ctl.delete)
            return
        if h["ready"] and p == "starting":
            state["power"] = "ready"
            state["ready_at"] = time.time()
            save_state()
            log("server ready", h.get("timings"), "nsfw:", h.get("nsfw"), h.get("nsfw_detail"))
            if h.get("wf_check"):
                for k, issues in h["wf_check"].items():
                    log("workflow check", k, "OK" if not issues else issues)
    except Exception as e:
        if not await laptop_online():
            live.update(detail="This laptop is offline. The server keeps working; waiting for the internet to come back.")
            return
        live["health_fail"] += 1
        if p == "starting":   # the tunnel address is known but not answering yet (new Cloudflare address, ~1-2 min)
            live.update(stage="booting", detail=f"Connecting to the Kaggle server ({int(time.time() - state.get('pushed_at', time.time()))} s)")
        if live["health_fail"] in (3, 10) or live["health_fail"] % 15 == 0:
            st = await blocking(kaggle_ctl.status)
            if st not in ("running", "queued"):
                requeue_unfinished("The Kaggle session ended")
                state.update(power="off", url=None, last_stop_reason=f"Kaggle session ended ({st})", stopped_at=time.time())
                save_state()
                return
        if live["health_fail"] > 40:
            state.update(power="error", error=f"Lost contact with the Kaggle server: {e}")
            save_state()
        return
    if state["power"] == "ready":
        active = bool(outbox) or any(j["status"] in ACTIVE for j in remote)
        if active:
            live["last_busy"] = time.time()
        quiet = min(time.time() - idle_since(), float(live.get("idle_s") or 1e9))   # phone activity counts too
        if not active and quiet > settings["idle_min"] * 60:
            await power_off(f"Auto-off after {settings['idle_min']} min without use")


# ------------------------------------------------------------------ sync with the shared queue on Kaggle
async def remote_upload(name):
    if name in remote_names:
        return remote_names[name]
    path = os.path.join(UPLOADS, os.path.basename(name))
    if not os.path.exists(path):
        raise RuntimeError("The photo is no longer on this laptop")
    data = aiohttp.FormData()
    data.add_field("file", open(path, "rb"), filename=os.path.basename(name))
    r = await gw_json("POST", "/api/upload", timeout=300, data=data)
    remote_names[name] = r["name"]
    return r["name"]


async def send(params):
    """Send one job request (as the user made it) to the gateway. Returns the gateway's job list."""
    body = {k: v for k, v in params.items() if k not in ("local_id", "note", "created", "image_local")}
    if params.get("image_local"):
        body["image"] = await remote_upload(params["image_local"])
    body["source"] = "laptop"
    r = await gw_json("POST", "/api/jobs", json=body, timeout=60)
    for j in r["jobs"]:
        j["local"] = {k: v for k, v in params.items() if k not in ("note",)}
    return r["jobs"]


async def sync_once():
    # 1) outbox -> gateway
    while outbox and state["power"] == "ready":
        item = outbox[0]
        try:
            made = await send(item)
            merge_remote(made)
        except GatewayError as e:
            if e.status in (400, 422):
                log("outbox item refused:", e)
                remote.append(failed_local(item, str(e)))
            else:
                raise
        outbox.pop(0)
        save_outbox()
    # 2) mirror the shared queue
    r = await gw_json("GET", f"/api/jobs?since={int(state.get('pushed_at', 0))}", timeout=30)
    merge_remote(r["jobs"], replace=True)
    gp = r.get("status", {}).get("gpus")
    if gp:
        live["gpus"] = gp
    # 3) download finished images (also those made from a phone)
    have = {x.get("remote_id") for x in library}
    for j in remote:
        if (j["status"] == "done" and j["kind"] != "improve" and j.get("result") and j["id"] not in have
                and j["id"] not in fetching and fetch_fail.get(j["id"], 0) < 5):
            fetching.add(j["id"])
            asyncio.create_task(download(j))


def failed_local(item, err):
    return {"id": "x" + uuid.uuid4().hex[:9], "kind": item.get("kind"), "status": "failed", "params": item, "created": time.time(),
            "finished": time.time(), "error": err, "gpu": None, "progress": 0}


def merge_remote(jobs_, replace=False):
    by = {j["id"]: j for j in remote}
    for j in jobs_:
        old = by.get(j["id"])
        if old and old.get("local") and not j.get("local"):
            j["local"] = old["local"]
        by[j["id"]] = j
    if replace:
        keep = {j["id"] for j in jobs_}
        by = {k: v for k, v in by.items() if k in keep or v["id"].startswith("x") or v["status"] not in ACTIVE}
    remote[:] = sorted(by.values(), key=lambda j: j["created"])[-300:]


def slug(text, n=5):
    return "-".join(re.findall(r"[a-zA-Z0-9]+", (text or "").lower())[:n]) or "image"


async def download(j):
    try:
        async with http.get(gw(f"/api/jobs/{j['id']}/image"), headers={"X-Key": state["key"]}, timeout=aiohttp.ClientTimeout(total=300)) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            data = await r.read()
        p = j["params"]
        loc = j.get("local") or {}
        tag = {"edit": "edit", "rembg": "nobg"}.get(j["kind"], p.get("quality", "turbo"))
        base = p.get("prompt") or (loc.get("title") or "image")
        name = f"{time.strftime('%Y-%m-%d_%H%M%S')}_{slug(base)}_{tag}_{p.get('seed', 0)}.png"
        with open(os.path.join(settings["output_dir"], name), "wb") as f:
            f.write(data)
        res = j.get("result") or {}
        item = {"id": uuid.uuid4().hex[:10], "remote_id": j["id"], "file": name, "kind": j["kind"], "created": time.time(),
                "favorite": False, "took": j.get("took"), "size": len(data), "w": res.get("w") or p.get("w"), "h": res.get("h") or p.get("h"),
                "prompt": p.get("prompt") or ("Background removed" if j["kind"] == "rembg" else ""), "negative": p.get("negative", ""),
                "aspect": p.get("aspect"), "quality": p.get("quality"), "seed": p.get("seed"), "nsfw": bool(p.get("nsfw")),
                "source_image": loc.get("image_local"), "from": j.get("source") or "phone"}
        library.append(item)
        save("library.json", library)
        await blocking(make_thumb, os.path.join(settings["output_dir"], name), os.path.join(THUMBS, item["id"] + ".jpg"))
        log("saved", name)
    except Exception as e:
        fetch_fail[j["id"]] = fetch_fail.get(j["id"], 0) + 1
        log("download failed", j["id"], e)
    finally:
        fetching.discard(j["id"])


async def sync_loop():
    while True:
        try:
            if state["power"] == "ready" and state.get("url"):
                await sync_once()
        except Exception as e:
            log("sync error", repr(e)[:200])
        await asyncio.sleep(2)


# ------------------------------------------------------------------ HTTP API for the UI
def is_local(req):
    return req.remote in ("127.0.0.1", "::1")


@web.middleware
async def auth_mw(req, handler):
    global last_user
    if not is_local(req) and req.path not in ("/login", "/style.css", "/favicon.svg"):
        if req.cookies.get("qis_pin") != settings["pin"]:
            if req.path.startswith("/api/"):
                return web.json_response({"error": "login"}, status=401)
            raise web.HTTPFound("/login")
    if req.path.startswith("/api/") and req.path != "/api/state" and req.method != "GET":
        last_user = time.time()
    return await handler(req)


LOGIN_HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Qwen Image Studio</title><link rel=icon href=/favicon.svg><link rel=stylesheet href=/style.css></head><body class=login-body><form method=post class=login-card>
<div class=logo>Qwen Image Studio</div><p>Enter the PIN shown in Settings on your laptop.</p>{err}
<input name=pin inputmode=numeric autocomplete=off autofocus placeholder=PIN><button class="btn primary">Open</button></form></body></html>"""


async def login_get(req):
    return web.Response(text=LOGIN_HTML.format(err=""), content_type="text/html")


async def login_post(req):
    f = await req.post()
    if f.get("pin", "").strip() == settings["pin"]:
        r = web.HTTPFound("/")
        r.set_cookie("qis_pin", settings["pin"], max_age=3600 * 24 * 365)
        raise r
    return web.Response(text=LOGIN_HTML.format(err="<p class=err>Wrong PIN</p>"), content_type="text/html")


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


async def get_quota():
    if not kaggle_ctl.signed_in():
        return None
    if time.time() - quota_cache["t"] > 300:
        try:
            quota_cache["v"] = await blocking(kaggle_ctl.quota)
        except Exception as e:
            log("quota failed", e)
        quota_cache["t"] = time.time()
    return quota_cache["v"]


def ui_jobs():
    lib_of = {x.get("remote_id"): x["id"] for x in library}
    out = []
    for i, o in enumerate(outbox):
        out.append({"id": o["local_id"], "kind": o.get("kind"), "status": "queued", "params": o, "created": o.get("created", 0),
                    "gpu": None, "progress": 0, "note": o.get("note") or "Waits for the server", "outbox": True,
                    "estimate": est(o)})
    for j in remote:
        if j["id"] in hidden:
            continue
        d = dict(j)
        d.pop("local", None)
        if d["status"] == "submitting":
            d["status"] = "running"
        if d["status"] == "done" and d["kind"] != "improve":
            d["result"] = lib_of.get(j["id"])
            if not d["result"]:
                d["status"] = "downloading"
        out.append(d)
    return out


def est(p):
    k = p.get("kind")
    if k == "create":
        w, h = SIZES.get(p.get("aspect"), (1024, 1024))
        return int((175 if p.get("quality") == "quality" else 26) * (0.35 + 0.65 * w * h / 1048576))
    return {"edit": 75, "rembg": 300, "improve": 390}.get(k, 60)


async def api_state(req):
    ip = lan_ip()
    return web.json_response({
        "power": state["power"], "error": state.get("error"), "last_stop_reason": state.get("last_stop_reason"),
        "live": {k: live.get(k) for k in ("stage", "pct", "detail", "gpus", "uptime", "idle_s", "timings", "pe", "nsfw", "nsfw_detail", "wf_check")},
        "ready_at": state.get("ready_at"), "pushed_at": state.get("pushed_at"),
        "jobs": ui_jobs()[-120:], "quota": await get_quota(),
        "idle_left": (max(0, int(settings["idle_min"] * 60 - (time.time() - idle_since()))) if state["power"] == "ready" else None),
        "settings": {**{k: v for k, v in settings.items() if (k != "pin" or is_local(req)) and k != "groq_api_key"},
                     "groq_set": bool(settings.get("groq_api_key"))},
        "lan_url": f"http://{ip}:{PORT}" if ip else None, "local": is_local(req), "sizes": SIZES,
        "kaggle": {"user": kaggle_ctl.USER, "kernel": kaggle_ctl.KERNEL, "signed_in": kaggle_ctl.signed_in(),
                   "source": kaggle_ctl.SOURCE}, "origin": state.get("origin"),
    })


async def api_power(req):
    d = await req.json()
    if d.get("on") and not kaggle_ctl.signed_in():
        return web.json_response({"error": "Sign in to Kaggle first", "signin": True}, status=409)
    asyncio.create_task(power_on() if d.get("on") else power_off("Turned off from the app"))
    return web.json_response({"ok": True})


def clean_request(d):
    """The job request as the gateway expects it (README: Gateway API)."""
    kind = d.get("kind")
    if kind not in ("create", "edit", "rembg"):
        raise ValueError("Unknown job")
    p = {"kind": kind, "seed": int(d["seed"]) if str(d.get("seed", "")).lstrip("-").isdigit() else -1}
    if kind in ("create", "edit"):
        p["prompt"] = (d.get("prompt") or "").strip()[:4000]
        if not p["prompt"]:
            raise ValueError("Write a prompt first" if kind == "create" else "Write what to change first")
        p["count"] = max(1, min(4, int(d.get("count") or 1)))
    if kind == "create":
        p.update(negative=str(d.get("negative") or "")[:2000], aspect=d.get("aspect") if d.get("aspect") in SIZES else "1:1",
                 quality="quality" if d.get("quality") == "quality" else "turbo",
                 nsfw=bool(settings.get("nsfw")), lora_strength=float(settings.get("nsfw_strength", 1.0)))
    if kind in ("edit", "rembg"):
        img = os.path.basename(d.get("image") or "")
        if not img or not os.path.exists(os.path.join(UPLOADS, img)):
            raise ValueError("Choose an image first")
        p["image_local"] = img
    refusal = safety_check(p.get("prompt", ""), p.get("nsfw", False))
    if refusal:
        raise PermissionError(refusal)
    return p


async def api_submit(req):
    d = await req.json()
    try:
        p = clean_request(d)
    except PermissionError as e:
        return web.json_response({"error": str(e), "blocked": True}, status=422)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    n = p.get("count", 1)
    if state["power"] == "ready" and state.get("url"):
        try:
            made = await send(p)
            merge_remote(made)
            return web.json_response({"jobs": [j["id"] for j in made], "sent": True})
        except GatewayError as e:
            if e.status in (400, 422):
                return web.json_response({"error": str(e), "blocked": e.status == 422}, status=e.status)
            log("send failed, keeping in outbox", e)
        except Exception as e:
            log("send failed, keeping in outbox", e)
    outbox.append({**p, "local_id": "o" + uuid.uuid4().hex[:9], "created": time.time()})
    save_outbox()
    return web.json_response({"jobs": [outbox[-1]["local_id"]], "count": n, "sent": False})


# ------------------------------------------------------------------ fast prompt helper (online AI, a few seconds; Kaggle helper is the fallback)
KEYS_ENV = os.path.expanduser("~/OneDrive/Documents/my-api-keys.env")
PE_TEXT = {}


def api_keys():
    path = settings.get("keys_env") or KEYS_ENV
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    if settings.get("groq_api_key"):          # the public app: the user's own free Groq key (Settings)
        out["GROQ_API_KEY"] = settings["groq_api_key"]
    return out


def pe_system(mode):
    if mode not in PE_TEXT:
        PE_TEXT[mode] = open(os.path.join(HERE, "prompts", f"pe_{mode}_system.txt"), encoding="utf-8").read()
    return PE_TEXT[mode]


REFUSAL_RE = re.compile(r"^\s*(i('| a)?m sorry|i can(no|')t|sorry,|i won'?t|as an ai)", re.I)


def clean_pe(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    return None if (len(text) < 40 or REFUSAL_RE.search(text)) else text


async def _chat(url, key, model, messages, extra=None):
    body = {"model": model, "messages": messages, "max_tokens": 1500, "temperature": 0.7, **(extra or {})}
    async with http.post(url, json=body, headers={"Authorization": "Bearer " + key, "User-Agent": "QwenImageStudio/1.0"},
                         timeout=aiohttp.ClientTimeout(total=40)) as r:
        if r.status != 200:
            raise RuntimeError(f"{r.status} {(await r.text())[:150]}")
        return (await r.json())["choices"][0]["message"]["content"]


async def online_improve(prompt, mode, aspect, image=None):
    """Returns (text, service) or (None, reasons). t2i: Groq -> Gemini -> OpenRouter. i2i (needs vision): Gemini -> OpenRouter."""
    import base64
    k = api_keys()
    user = prompt + (f" (aspect ratio {aspect})" if mode == "t2i" and aspect else "")
    sysm = pe_system(mode)
    img_b64 = None
    if image:
        with open(os.path.join(UPLOADS, image), "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(image)[1].lower().lstrip(".")
        mime = "image/" + ("jpeg" if ext in ("jpg", "jpeg") else ext or "png")
    tries = []
    if mode == "t2i" and k.get("GROQ_API_KEY"):
        tries.append(("Groq", lambda: _chat("https://api.groq.com/openai/v1/chat/completions", k["GROQ_API_KEY"], "qwen/qwen3.8-27b",
                     [{"role": "system", "content": sysm}, {"role": "user", "content": user}], {"reasoning_effort": "none"})))
    gem = k.get("GEMINI_FREE_API_KEY")
    if gem:
        content = [{"type": "text", "text": user}]
        if img_b64:
            content.insert(0, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}})
        for m in ("gemini-3.5-flash", "gemini-3.8-flash"):
            tries.append((f"Gemini", lambda m=m: _chat("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", gem, m,
                         [{"role": "system", "content": sysm}, {"role": "user", "content": content}], {"reasoning_effort": "none"})))
    if k.get("OPENROUTER_API_KEY") and not img_b64:
        tries.append(("OpenRouter", lambda: _chat("https://openrouter.ai/api/v1/chat/completions", k["OPENROUTER_API_KEY"], "openrouter/free",
                     [{"role": "system", "content": sysm}, {"role": "user", "content": user}])))
    why = []
    for name, call in tries:
        try:
            text = clean_pe(await call())
            if text:
                return text, name
            why.append(f"{name}: refused/empty")
        except Exception as e:
            why.append(f"{name}: {str(e)[:80]}")
    return None, why


async def api_improve(req):
    d = await req.json()
    prompt = (d.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "Write a short idea first"}, status=400)
    mode = "i2i" if d.get("mode") == "i2i" else "t2i"
    refusal = safety_check(prompt, bool(settings.get("nsfw")) and mode == "t2i")
    if refusal:
        return web.json_response({"error": refusal, "blocked": True}, status=422)
    img = os.path.basename(d.get("image") or "") if mode == "i2i" else None
    if mode == "i2i" and (not img or not os.path.exists(os.path.join(UPLOADS, img))):
        return web.json_response({"error": "Choose an image first"}, status=400)
    t0 = time.time()
    try:
        text, by = await online_improve(prompt, mode, d.get("aspect"), img)
    except Exception as e:
        text, by = None, [str(e)]
    if text:
        return web.json_response({"text": text, "by": by, "took": round(time.time() - t0, 1)})
    log("online prompt helper failed:", by)
    if state["power"] != "ready" or not state.get("url"):
        return web.json_response({"error": "The quick prompt helper is not set up. Turn the server on to use the slower one on the Kaggle GPUs, "
                                           "or add a free Groq API key in Settings (then it takes about 1 second)."}, status=409)
    body = {"kind": "improve", "prompt": prompt, "mode": mode, "aspect": d.get("aspect"), "source": "laptop",
            "nsfw": bool(settings.get("nsfw")) and mode == "t2i"}
    try:
        if mode == "i2i":
            body["image"] = await remote_upload(img)
        r = await gw_json("POST", "/api/jobs", json=body, timeout=60)
        merge_remote(r["jobs"])
        pe_ready = live.get("pe", {}).get(mode) is True
        return web.json_response({"id": r["jobs"][0]["id"], "pe_ready": pe_ready})
    except GatewayError as e:
        return web.json_response({"error": str(e), "blocked": e.status == 422}, status=e.status if e.status < 500 else 502)
    except Exception as e:
        return web.json_response({"error": f"Could not reach the server: {e}"}, status=502)


async def api_improve_get(req):
    try:
        return web.json_response(await gw_json("GET", f"/api/jobs/{req.match_info['id']}"))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)


async def api_cancel(req):
    jid = req.match_info["id"]
    o = next((x for x in outbox if x["local_id"] == jid), None)
    if o:
        outbox.remove(o)
        save_outbox()
        return web.json_response({"ok": True})
    try:
        merge_remote([await gw_json("POST", f"/api/jobs/{jid}/cancel")])
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)
    return web.json_response({"ok": True})


async def api_retry(req):
    jid = req.match_info["id"]
    j = next((x for x in remote if x["id"] == jid), None)
    if not j:
        return web.json_response({"error": "unknown job"}, status=404)
    src = j.get("local") or {**j["params"], "kind": j["kind"]}
    try:
        p = clean_request({**src, "image": src.get("image_local") or src.get("image"), "count": 1})
    except (ValueError, PermissionError) as e:
        return web.json_response({"error": str(e)}, status=400)
    outbox.append({**p, "local_id": "o" + uuid.uuid4().hex[:9], "created": time.time()})
    save_outbox()
    return web.json_response({"ok": True})


async def api_clear_jobs(req):
    saved = {x.get("remote_id") for x in library}
    gone = [j["id"] for j in remote if j["status"] not in ACTIVE and not (j["status"] == "done" and j["kind"] != "improve" and j["id"] not in saved)]
    hidden.update(gone)
    for jid in gone:            # finished jobs (their images are already saved here): remove them from the shared queue too
        await gw_delete(jid)
    return web.json_response({"ok": True})


async def api_library(req):
    items = [x for x in library if os.path.exists(os.path.join(settings["output_dir"], x["file"]))]
    return web.json_response(sorted(items, key=lambda x: -x["created"]))


async def api_lib_update(req):
    x = lib_by_id(req.match_info["id"])
    if not x:
        return web.json_response({"error": "unknown"}, status=404)
    d = await req.json()
    if "favorite" in d:
        x["favorite"] = bool(d["favorite"])
    save("library.json", library)
    return web.json_response(x)


async def gw_delete(remote_id):
    """Delete a job and its image on the Kaggle server (so no app downloads it again)."""
    if not remote_id or state["power"] not in ("ready", "starting") or not state.get("url"):
        return False
    try:
        await gw_json("DELETE", f"/api/jobs/{remote_id}", timeout=20)
        return True
    except Exception as e:
        log("remote delete failed", remote_id, e)
        return False


async def api_lib_delete(req):
    x = lib_by_id(req.match_info["id"])
    if x:
        await gw_delete(x.get("remote_id"))
        try:
            os.remove(os.path.join(settings["output_dir"], x["file"]))
        except FileNotFoundError:
            pass
        try:
            os.remove(os.path.join(THUMBS, x["id"] + ".jpg"))
        except FileNotFoundError:
            pass
        library.remove(x)
        save("library.json", library)
    return web.json_response({"ok": True})


async def api_lib_to_upload(req):
    """Use a finished image as the input of Edit / Remove background."""
    x = lib_by_id(req.match_info["id"])
    if not x:
        return web.json_response({"error": "unknown"}, status=404)
    name = f"img_{uuid.uuid4().hex[:10]}.png"
    shutil.copyfile(os.path.join(settings["output_dir"], x["file"]), os.path.join(UPLOADS, name))
    return web.json_response({"name": name})


async def api_upload(req):
    reader = await req.multipart()
    part = await reader.next()
    ext = os.path.splitext(part.filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        return web.json_response({"error": "Use a PNG, JPG or WEBP image"}, status=400)
    name = f"img_{uuid.uuid4().hex[:10]}{ext}"
    size = 0
    with open(os.path.join(UPLOADS, name), "wb") as f:
        while True:
            chunk = await part.read_chunk(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > 40 * 1024 ** 2:
                f.close()
                os.remove(os.path.join(UPLOADS, name))
                return web.json_response({"error": "That image is larger than 40 MB"}, status=400)
            f.write(chunk)
    return web.json_response({"name": name})


async def api_settings(req):
    d = await req.json()
    for k in ("idle_min", "max_hours"):
        if k in d:
            settings[k] = max(1, int(d[k]))
    if d.get("aspect") in SIZES:
        settings["aspect"] = d["aspect"]
    if d.get("quality") in ("turbo", "quality"):
        settings["quality"] = d["quality"]
    if "count" in d:
        settings["count"] = max(1, min(4, int(d["count"])))
    if d.get("theme") in ("dark", "light"):
        settings["theme"] = d["theme"]
    if "nsfw" in d:
        settings["nsfw"] = bool(d["nsfw"])
    if "nsfw_strength" in d:
        settings["nsfw_strength"] = round(max(0.0, min(2.0, float(d["nsfw_strength"]))), 2)
    if is_local(req):
        for k in ("nsfw_lora_repo", "nsfw_lora_file"):
            if k in d and re.fullmatch(r"[^\"\\\n]{0,200}", str(d[k])):
                settings[k] = str(d[k]).strip()
        if "pin" in d and re.fullmatch(r"\d{4,8}", str(d["pin"])):
            settings["pin"] = str(d["pin"])
        if "groq_api_key" in d and re.fullmatch(r"[A-Za-z0-9_\-]{0,200}", str(d["groq_api_key"]).strip()):
            settings["groq_api_key"] = str(d["groq_api_key"]).strip()
    save("settings.json", settings)
    return web.json_response({"ok": True})


async def api_signin(req):
    """The sign-in page (laptop only): checks the token with Kaggle, then keeps it in data/kaggle_token."""
    if not is_local(req):
        return web.json_response({"error": "Sign in on the computer"}, status=403)
    d = await req.json()
    try:
        user, token = await blocking(kaggle_ctl.check_token, str(d.get("username") or ""), str(d.get("token") or ""))
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        return web.json_response({"error": f"Could not reach Kaggle: {e}"}, status=502)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,80}", user):
        return web.json_response({"error": "That username looks wrong"}, status=400)
    settings["kaggle_user"] = user
    save("settings.json", settings)
    kaggle_ctl.save_sign_in(user, token)
    quota_cache["t"] = 0
    log("signed in to Kaggle as", user)
    return web.json_response({"ok": True, "user": user})


async def api_signout(req):
    if not is_local(req):
        return web.json_response({"error": "Sign out on the computer"}, status=403)
    if state["power"] in ("starting", "ready", "stopping"):
        return web.json_response({"error": "Turn the server off first"}, status=409)
    settings.pop("kaggle_user", None)
    save("settings.json", settings)
    kaggle_ctl.sign_out()
    quota_cache.update(t=0, v=None)
    log("signed out of Kaggle")
    return web.json_response({"ok": True})


async def api_pairing(req):
    """For a future phone app: what it needs to find and use the server without the laptop (laptop only)."""
    if not is_local(req):
        return web.json_response({"error": "laptop only"}, status=403)
    return web.json_response({"ntfy_topic": state["topic"], "key": state["key"], "url": state.get("url"),
                              "kaggle_user": kaggle_ctl.USER, "kernel": kaggle_ctl.KERNEL})


show_window = None  # set by app_main.py when running as the Windows app


async def api_show(req):
    if show_window:
        show_window()
    return web.json_response({"shown": bool(show_window)})


async def api_open_folder(req):
    if is_local(req):
        os.startfile(settings["output_dir"])
    return web.json_response({"ok": True})


async def media(req):
    name = os.path.basename(req.match_info["name"])
    p = os.path.join(settings["output_dir"], name)
    if not os.path.exists(p):
        raise web.HTTPNotFound()
    headers = {"Content-Disposition": f'attachment; filename="{name}"'} if req.query.get("dl") else {}
    return web.FileResponse(p, headers=headers)


async def thumb(req):
    lid = os.path.basename(req.match_info["name"]).rsplit(".", 1)[0]
    p = os.path.join(THUMBS, lid + ".jpg")
    if os.path.exists(p):
        return web.FileResponse(p)
    x = lib_by_id(lid)                       # no ffmpeg: fall back to the full image
    if x and os.path.exists(os.path.join(settings["output_dir"], x["file"])):
        return web.FileResponse(os.path.join(settings["output_dir"], x["file"]))
    raise web.HTTPNotFound()


async def upload_file(req):
    p = os.path.join(UPLOADS, os.path.basename(req.match_info["name"]))
    if not os.path.exists(p):
        raise web.HTTPNotFound()
    return web.FileResponse(p)


async def index(req):
    return web.FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-cache"})


async def on_start(app):
    global http
    http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    if state["power"] in ("stopping", "error"):
        state.update(power="off", url=None, error=None, last_stop_reason=state.get("last_stop_reason") or "Turned off")
        save_state()
        asyncio.get_running_loop().run_in_executor(POOL, kaggle_ctl.delete)
    asyncio.get_running_loop().run_in_executor(POOL, make_all_thumbs)
    asyncio.create_task(power_loop())
    asyncio.create_task(sync_loop())
    log(f"Qwen Image Studio on http://localhost:{PORT}  (LAN: {lan_ip()}:{PORT})  power={state['power']}  kernel={kaggle_ctl.KERNEL}")


async def on_stop(app):
    if http:
        await http.close()


def make_app():
    app = web.Application(middlewares=[auth_mw], client_max_size=50 * 1024 ** 2)
    app.add_routes([
        web.get("/", index), web.get("/login", login_get), web.post("/login", login_post),
        web.get("/api/state", api_state), web.post("/api/power", api_power),
        web.post("/api/submit", api_submit), web.post("/api/improve", api_improve), web.get("/api/improve/{id}", api_improve_get),
        web.post("/api/jobs/{id}/cancel", api_cancel), web.post("/api/jobs/{id}/retry", api_retry), web.post("/api/jobs/clear", api_clear_jobs),
        web.get("/api/library", api_library), web.post("/api/library/{id}", api_lib_update), web.delete("/api/library/{id}", api_lib_delete),
        web.post("/api/library/{id}/use", api_lib_to_upload),
        web.post("/api/upload", api_upload), web.post("/api/settings", api_settings), web.get("/api/pairing", api_pairing),
        web.post("/api/signin", api_signin), web.post("/api/signout", api_signout),
        web.post("/api/open-folder", api_open_folder), web.post("/api/show", api_show),
        web.get("/media/{name}", media), web.get("/thumbs/{name}", thumb), web.get("/uploads/{name}", upload_file),
        web.static("/", WEB),
    ])
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=PORT, print=None, access_log=None)

"""Stand-in for kaggle_ctl.py in local tests (server.py with QIS_FAKE_KAGGLE=1).
push() runs kaggle/qis_server.py ON THIS LAPTOP in test mode (QIS_TEST=1, fake ComfyUI, no GPU, no Kaggle).
messages() plays the ntfy topic: it announces any gateway answering on the test port, including one started
by someone else (tests start a "phone" session themselves and write its PID to data/_fake_kernel/external.pid).
Nothing here talks to Kaggle."""
import os, secrets, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER, SLUG = "local-test", "fake-kernel"
KERNEL = f"{USER}/{SLUG}"
GW_PORT = os.environ.get("QIS_GATEWAY_PORT", "8790")
WORK = os.path.join(HERE, "data", "_fake_kernel")
_proc = {"p": None, "t": 0}
_events = []          # the fake ntfy topic
PUSHES = []           # times push() was called (tests check that joining does not push)


def new_key():
    return secrets.token_urlsafe(24)


def kernel_env(key, idle_min=30):
    os.makedirs(WORK, exist_ok=True)
    return {**os.environ, "QIS_TEST": "1", "QIS_KEY": key, "QIS_GATEWAY_PORT": GW_PORT, "QIS_PORTS": "8791,8792",
            "QIS_WORKDIR": WORK, "QIS_IDLE_MIN": str(idle_min), "PYTHONDONTWRITEBYTECODE": "1"}


def push(key, topic, idle_min, max_hours, nsfw_repo="", nsfw_file=""):
    PUSHES.append(time.time())
    delete()
    _proc["p"] = subprocess.Popen([sys.executable, "-B", os.path.join(HERE, "kaggle", "qis_server.py")], env=kernel_env(key, idle_min), cwd=HERE,
                                  stdout=open(os.path.join(WORK, "kernel.log"), "w"), stderr=subprocess.STDOUT,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _proc["t"] = time.time()
    return {"version": 1, "url": "local"}


def _gateway_up():
    try:
        with socket.create_connection(("127.0.0.1", int(GW_PORT)), timeout=0.5):
            return True
    except OSError:
        return False


def messages():
    up = _gateway_up()
    last = _events[-1]["type"] if _events else None
    if up and last != "url":
        _events.append({"type": "url", "url": f"http://127.0.0.1:{GW_PORT}", "t": time.time()})
    elif not up and last == "url":
        _events.append({"type": "stopped", "reason": "gone", "t": time.time()})
    return list(_events)


def status():
    p = _proc["p"]
    return "running" if (p and p.poll() is None) or _gateway_up() else "none"


def _kill(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def delete():
    hit = False
    p = _proc["p"]
    if p and p.poll() is None:
        _kill(p.pid)          # the kernel started the fake ComfyUIs as its children; kill the whole tree
        hit = True
    ext = os.path.join(WORK, "external.pid")
    if os.path.exists(ext):
        _kill(open(ext).read().strip())
        os.remove(ext)
        hit = True
    return hit


def quota():
    return {"used_h": 3.5, "reserved_h": 0.0, "total_h": 30.0, "refresh": "2026-11-08T00:00:00"}

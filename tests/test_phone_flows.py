"""Local test (no Kaggle, no GPU) for the flows a phone app needs from the Windows app and the gateway:
 A. pressing On while a server started from the phone is running JOINS it (no push, no kill)
 B. while off, the app notices a phone-started server by itself: sends its waiting jobs, downloads the phone's images
 C. DELETE /api/jobs/{id} removes a job + its image; the app's Delete and "Clear finished" use it
 D. fetching images does not count as activity for the idle timer; submits do
Runs everything in a scratch copy of the project. Usage: <python> -B tests/test_phone_flows.py SCRATCH_DIR"""
import json, os, shutil, subprocess, sys, time, urllib.error, urllib.request, uuid

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = os.path.abspath(sys.argv[1])
UI = "http://127.0.0.1:8766"
PY = sys.executable
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
OK, FAIL = [], []
procs = []


def check(name, cond, info=""):
    (OK if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:300]}]"), flush=True)


def req(url, data=None, method=None, headers=None, raw=False):
    h = dict(headers or {})
    body = None
    if data is not None:
        body, h["Content-Type"] = json.dumps(data).encode(), "application/json"
    r = urllib.request.Request(url, data=body, method=method or ("POST" if body is not None else "GET"), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            b = resp.read()
            return resp.status, (b if raw else json.loads(b or b"null"))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b
    except Exception as e:
        return 0, str(e)


def wait(fn, timeout, step=1.0):
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def kill(p):
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)


def fresh_home():
    shutil.rmtree(T, ignore_errors=True)
    os.makedirs(os.path.join(T, "data"))
    for d in ("kaggle", "prompts", "tools", "web"):
        shutil.copytree(os.path.join(SRC, d), os.path.join(T, d), ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("server.py", "kaggle_ctl.py"):
        shutil.copy(os.path.join(SRC, f), T)
    os.makedirs(os.path.join(T, "pics"))
    json.dump({"output_dir": os.path.join(T, "pics"), "pin": "2468"}, open(os.path.join(T, "data", "settings.json"), "w"))


def start_server(off_poll):
    env = {**os.environ, "QIS_HOME": T, "QIS_FAKE_KAGGLE": "1", "FAKE_DELAY": "3", "QIS_OFF_POLL": str(off_poll), "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.Popen([PY, "-B", os.path.join(T, "server.py")], cwd=T, env=env, stdout=open(os.path.join(T, "data", f"server_{off_poll}.log"), "w"),
                         stderr=subprocess.STDOUT, creationflags=NOWIN)
    procs.append(p)
    wait(lambda: req(UI + "/api/state")[0] == 200, 30)
    return p


def start_phone_kernel(key):
    """What the phone app's On button does: a kernel with the same key + topic, without the laptop."""
    sys.path.insert(0, os.path.join(T, "tools"))
    import fake_kaggle
    p = subprocess.Popen([PY, "-B", os.path.join(T, "kaggle", "qis_server.py")], env=fake_kaggle.kernel_env(key), cwd=T,
                         stdout=open(os.path.join(fake_kaggle.WORK, "phone_kernel.log"), "w"), stderr=subprocess.STDOUT, creationflags=NOWIN)
    procs.append(p)
    open(os.path.join(fake_kaggle.WORK, "external.pid"), "w").write(str(p.pid))
    gw = f"http://127.0.0.1:{fake_kaggle.GW_PORT}"
    up = wait(lambda: (lambda r: r[0] == 200 and r[1].get("ready"))(req(gw + "/health", headers={"X-Key": key})), 60)
    return gw, bool(up)


def log_text(off_poll):
    return open(os.path.join(T, "data", f"server_{off_poll}.log"), encoding="utf-8", errors="replace").read()


try:
    # ------------------------------------------------------------ A. power on joins a phone-started server
    fresh_home()
    srv = start_server(off_poll=100000)          # no background watching in this part
    pair = req(UI + "/api/pairing")[1]
    KEY = {"X-Key": pair["key"]}
    gw, up = start_phone_kernel(pair["key"])
    check("A: phone-started server is up", up)
    up0 = req(gw + "/health", headers=KEY)[1]["uptime"]
    req(UI + "/api/power", {"on": True})
    s = wait(lambda: (lambda x: x if x["power"] == "ready" else None)(req(UI + "/api/state")[1]), 20)
    check("A: On -> ready by joining", s is not None and s.get("origin") == "joined", s and (s["power"], s.get("origin")))
    lg = log_text(100000)
    check("A: joined, no new push", "joined the running server" in lg and "power_on failed" not in lg, lg[-400:])
    up1 = req(gw + "/health", headers=KEY)[1].get("uptime", 0)
    check("A: the phone's session was not restarted", up1 >= up0, (up0, up1))
    req(UI + "/api/power", {"on": False})
    off = wait(lambda: req(UI + "/api/state")[1]["power"] == "off", 30)
    check("A: power off stops it", off and wait(lambda: req(gw + "/health", headers=KEY)[0] != 200, 15))
    kill(srv)
    time.sleep(1)

    # ------------------------------------------------------------ B. while off, the app attaches by itself
    fresh_home()
    srv = start_server(off_poll=3)
    pair = req(UI + "/api/pairing")[1]
    KEY = {"X-Key": pair["key"]}
    c, r = req(UI + "/api/submit", {"kind": "create", "prompt": "a teapot made while the server was off", "count": 1})
    check("B: laptop job waits in the outbox", c == 200 and r.get("sent") is False, r)
    time.sleep(4)
    check("B: stays off while nothing runs", req(UI + "/api/state")[1]["power"] == "off")
    gw, up = start_phone_kernel(pair["key"])
    c, r = req(gw + "/api/jobs", {"kind": "create", "prompt": "a lighthouse made on the phone", "source": "phone"}, headers=KEY)
    phone_job = r["jobs"][0]["id"] if c == 200 else None
    s = wait(lambda: (lambda x: x if x["power"] == "ready" else None)(req(UI + "/api/state")[1]), 30)
    check("B: app attached to the phone's server without pressing On", s is not None and s.get("origin") == "joined", s and s["power"])
    lib = wait(lambda: (lambda L: L if len(L) >= 2 else None)(req(UI + "/api/library")[1]), 40)
    lib = lib or req(UI + "/api/library")[1]
    check("B: outbox job sent and saved", any(x["prompt"].startswith("a teapot") for x in lib), [x["prompt"] for x in lib])
    check("B: phone's image downloaded", any(x.get("remote_id") == phone_job and x.get("from") == "phone" for x in lib), lib)

    # ------------------------------------------------------------ D. idle timer: images don't count, submits do
    time.sleep(3)
    i1 = req(gw + "/health", headers=KEY)[1]["idle_s"]
    req(gw + f"/api/jobs/{phone_job}/image", headers=KEY, raw=True)
    req(gw + "/api/jobs", headers=KEY)
    time.sleep(1.5)
    i2 = req(gw + "/health", headers=KEY)[1]["idle_s"]
    check("D: fetching an image / polling does not reset idle", i2 >= i1 + 1, (i1, i2))
    req(gw + "/api/jobs", {"kind": "create", "prompt": "a red kite", "source": "phone"}, headers=KEY)
    i3 = req(gw + "/health", headers=KEY)[1]["idle_s"]
    check("D: a submit resets idle", i3 <= 1, (i2, i3))
    wait(lambda: not any(j["status"] in ("queued", "running", "downloading") for j in req(UI + "/api/state")[1]["jobs"]), 40)

    # ------------------------------------------------------------ C. delete
    c, r = req(gw + f"/api/jobs/{phone_job}", method="DELETE", headers=KEY)
    check("C: DELETE /api/jobs/{id}", c == 200 and r.get("deleted"), r)
    check("C: job is gone", req(gw + f"/api/jobs/{phone_job}", headers=KEY)[0] == 404)
    check("C: its image is gone", req(gw + f"/api/jobs/{phone_job}/image", headers=KEY, raw=True)[0] == 404)
    c, r = req(gw + "/api/jobs/doesnotexist/delete", {}, headers=KEY)
    check("C: deleting an unknown job is harmless", c == 200 and r.get("deleted") is False, r)
    lib = req(UI + "/api/library")[1]
    tea = next(x for x in lib if x["prompt"].startswith("a teapot"))
    req(UI + f"/api/library/{tea['id']}", method="DELETE")
    check("C: app Delete also deletes it on the server", req(gw + f"/api/jobs/{tea['remote_id']}", headers=KEY)[0] == 404)
    c, r = req(gw + "/api/jobs", {"kind": "create", "prompt": "a queued boat", "count": 3, "source": "phone"}, headers=KEY)
    last = r["jobs"][-1]["id"]
    c, r = req(gw + f"/api/jobs/{last}", method="DELETE", headers=KEY)
    time.sleep(2)
    check("C: deleting a queued job cancels and removes it", req(gw + f"/api/jobs/{last}", headers=KEY)[0] == 404, r)
    wait(lambda: not any(j["status"] in ("queued", "running", "downloading") for j in req(UI + "/api/state")[1]["jobs"]), 40)
    finished = [j["id"] for j in req(gw + "/api/jobs", headers=KEY)[1]["jobs"]]
    req(UI + "/api/jobs/clear", {})
    time.sleep(1)
    left = [j["id"] for j in req(gw + "/api/jobs", headers=KEY)[1]["jobs"]]
    check("C: Clear finished removes saved jobs from the server", finished and not left, (len(finished), left))

    # ------------------------------------------------------------ power off, and no re-attach to a dead server
    req(UI + "/api/power", {"on": False})
    off = wait(lambda: req(UI + "/api/state")[1]["power"] == "off", 30)
    time.sleep(8)    # > 2 off-polls
    check("B: off stays off after the laptop turned it off", off and req(UI + "/api/state")[1]["power"] == "off")
    check("B: the session is gone", req(gw + "/health", headers=KEY)[0] != 200)
finally:
    for p in procs:
        kill(p)
    print(f"\n{len(OK)} passed, {len(FAIL)} failed", flush=True)
    sys.exit(1 if FAIL else 0)

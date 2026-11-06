"""Local test of the public app's Kaggle sign-in (no kernel is ever pushed).
Runs server.py from a scratch copy with an EMPTY user profile, so no personal token is found: the app must start
signed out, refuse to power on, refuse a bad token, keep a Groq key private, and sign out cleanly.
Optional: QIS_TEST_TOKEN_FILE=<file with a real token> also checks a successful sign-in (read-only Kaggle calls:
token check + GPU quota). Usage: <python> -B tests/test_signin.py SCRATCH_DIR"""
import json, os, shutil, subprocess, sys, time, urllib.error, urllib.request

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = os.path.abspath(sys.argv[1])
PORT = "8777"
UI = f"http://127.0.0.1:{PORT}"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
OK, FAIL = [], []


def check(name, cond, info=""):
    (OK if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{str(info)[:300]}]"), flush=True)


def req(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(UI + path, data=body, method="POST" if body is not None else "GET",
                               headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None


shutil.rmtree(T, ignore_errors=True)
home, app = os.path.join(T, "home"), os.path.join(T, "app")
os.makedirs(home)
os.makedirs(os.path.join(app, "data"))
for d in ("kaggle", "prompts", "web"):
    shutil.copytree(os.path.join(SRC, d), os.path.join(app, d), ignore=shutil.ignore_patterns("__pycache__"))
for f in ("server.py", "kaggle_ctl.py"):
    shutil.copy(os.path.join(SRC, f), app)
data = os.path.join(T, "appdata")          # like the portable build: data outside the program folder
os.makedirs(data)
json.dump({"output_dir": os.path.join(T, "pics"), "pin": "1357"}, open(os.path.join(data, "settings.json"), "w"))
env = {k: v for k, v in os.environ.items() if not k.startswith("KAGGLE_")}
env.update(QIS_HOME=app, QIS_DATA=data, QIS_PORT=PORT, USERPROFILE=home, HOME=home, PYTHONDONTWRITEBYTECODE="1")
log = open(os.path.join(T, "server.log"), "w")
p = subprocess.Popen([sys.executable, "-B", os.path.join(app, "server.py")], cwd=app, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=NOWIN)
try:
    t0 = time.time()
    while time.time() - t0 < 30:
        try:
            if req("/api/state")[0] == 200:
                break
        except Exception:
            time.sleep(0.5)
    code, s = req("/api/state")
    check("starts signed out", code == 200 and s["kaggle"]["signed_in"] is False and s["kaggle"]["kernel"] is None, s and s.get("kaggle"))
    check("no quota while signed out", s["quota"] is None, s["quota"])
    check("data folder is outside the program folder", os.path.exists(os.path.join(data, "state.json")) and not os.path.exists(os.path.join(app, "data", "state.json")))
    code, r = req("/api/power", {"on": True})
    check("power on refused while signed out", code == 409 and r.get("signin"), (code, r))
    time.sleep(1)
    check("still off", req("/api/state")[1]["power"] == "off")
    code, r = req("/api/signin", {"username": "nobody", "token": ""})
    check("empty token refused", code == 400, (code, r))
    code, r = req("/api/signin", {"username": "nobody", "token": "KGAT_" + "0" * 32})
    check("bad token refused by Kaggle", code == 400 and "did not accept" in (r or {}).get("error", ""), (code, r))
    check("no token file after a refused sign-in", not os.path.exists(os.path.join(data, "kaggle_token")))
    code, r = req("/api/settings", {"groq_api_key": "gsk_testkey_1234567890"})
    s = req("/api/state")[1]
    check("groq key saved, never sent to the UI", s["settings"].get("groq_set") is True and "groq_api_key" not in s["settings"]
          and json.load(open(os.path.join(data, "settings.json")))["groq_api_key"] == "gsk_testkey_1234567890", s["settings"])
    req("/api/settings", {"groq_api_key": ""})
    check("groq key removed", req("/api/state")[1]["settings"]["groq_set"] is False)
    code, r = req("/api/signout", {})
    check("sign out while signed out is harmless", code == 200, (code, r))

    tf = os.environ.get("QIS_TEST_TOKEN_FILE")
    if tf:
        token = open(os.path.expanduser(tf), encoding="utf-8").read().strip()
        code, r = req("/api/signin", {"username": "", "token": token})
        check("real token accepted, username found by Kaggle", code == 200 and r.get("user"), (code, r))
        s = req("/api/state")[1]
        check("signed in: kernel in the user's own account", s["kaggle"]["signed_in"] and s["kaggle"]["source"] == "app"
              and s["kaggle"]["kernel"] == f"{r.get('user')}/qwen-image-studio-server", s["kaggle"])
        check("token kept in the data folder", open(os.path.join(data, "kaggle_token"), encoding="utf-8").read() == token)
        check("kaggle library works with it (GPU quota)", bool(s["quota"]) and s["quota"].get("total_h", 0) > 0, s["quota"])
        code, r = req("/api/signout", {})
        s = req("/api/state")[1]
        check("sign out removes the token", code == 200 and not s["kaggle"]["signed_in"] and not os.path.exists(os.path.join(data, "kaggle_token")), (code, s["kaggle"]))
finally:
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)
    log.close()
    shutil.rmtree(os.path.join(T, "appdata"), ignore_errors=True)    # never leave a token copy behind
print(f"\n{len(OK)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)

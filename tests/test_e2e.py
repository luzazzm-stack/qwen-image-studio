"""End-to-end test against a running local server started with QIS_FAKE_KAGGLE=1 (no Kaggle, no GPU):
UI API on :8766 -> outbox -> power on (kernel runs locally in test mode with fake ComfyUI) -> shared queue on the
gateway (:8790) -> results downloaded into the library. Also checks the gateway API directly (X-Key, safety block).
Run: <python> -B tests/test_e2e.py"""
import json, os, sys, time, urllib.error, urllib.request, uuid

UI = "http://127.0.0.1:8766"
OK, FAIL = [], []


def req(url, data=None, method=None, headers=None, raw=False, files=None):
    h = dict(headers or {})
    body = None
    if files:
        b = uuid.uuid4().hex
        name, content = files
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
        h["Content-Type"] = f"multipart/form-data; boundary={b}"
    elif data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, method=method or ("POST" if body is not None else "GET"), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            b = resp.read()
            return resp.status, (b if raw else json.loads(b or b"null"))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b


def check(name, cond, info=""):
    (OK if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(info)[:300]}]" if not cond else ""), flush=True)


def wait(fn, timeout, step=1.0):
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "fake_comfy.py"), encoding="utf-8").read()
_ns = {}
exec(_src.split("OI = {")[0].split("PORT, OUT")[0] + "\n" + _src[_src.index("def png("):_src.index("def validate(")], _ns)
PNG = _ns["png"](64, 48, (200, 80, 40))

st = lambda: req(UI + "/api/state")[1]
s0 = st()
check("state answers, server off", s0["power"] in ("off",), s0["power"])

# 1) jobs made while off go to the outbox
c, r = req(UI + "/api/submit", {"kind": "create", "prompt": "a red teapot on a wooden table, studio photo", "aspect": "3:4", "count": 2})
check("create while off -> outbox", c == 200 and r.get("sent") is False, r)
c, r = req(UI + "/api/submit", {"kind": "create", "prompt": "nude teen on a beach"})
check("minors safety block on laptop (422)", c == 422 and r.get("blocked"), r)
c, up = req(UI + "/api/upload", files=("photo.png", PNG))
check("upload photo", c == 200 and up.get("name"), up)
c, r = req(UI + "/api/submit", {"kind": "edit", "prompt": "make it a moonlit night", "image": up["name"]})
check("edit while off -> outbox", c == 200, r)
c, r = req(UI + "/api/submit", {"kind": "rembg", "image": up["name"]})
check("rembg while off -> outbox", c == 200, r)
c, r = req(UI + "/api/submit", {"kind": "edit", "prompt": "", "image": up["name"]})
check("edit without instruction refused", c == 400, r)
c, r = req(UI + "/api/improve", {"prompt": "a cat"})
check("prompt helper refuses while off (409)", c == 409, r)
s = st()
check("outbox shows 3 waiting jobs", sum(1 for j in s["jobs"] if j.get("outbox")) == 3, [j.get("status") for j in s["jobs"]])

# 2) power on -> ready
req(UI + "/api/power", {"on": True})
s = wait(lambda: (lambda x: x if x["power"] == "ready" else None)(st()), 90)
check("power on -> ready (fake kernel)", s is not None, st().get("live"))
s = st()
check("workflow self-test has no issues", s["live"].get("wf_check") and all(not v for v in s["live"]["wf_check"].values()), s["live"].get("wf_check"))
check("NSFW LoRA self-test ok", s["live"].get("nsfw") == "ok", (s["live"].get("nsfw"), s["live"].get("nsfw_detail")))
check("prompt helper models ready", s["live"].get("pe") == {"t2i": True, "i2i": True}, s["live"].get("pe"))

# 3) outbox flushes, both GPUs work, results download
lib = wait(lambda: (lambda L: L if len(L) >= 4 else None)(req(UI + "/api/library")[1]), 90)
check("4 images made and saved (2 create + edit + rembg)", lib is not None and len(lib) == 4, len(req(UI + "/api/library")[1]))
lib = req(UI + "/api/library")[1]
kinds = sorted(x["kind"] for x in lib)
check("library kinds", kinds == ["create", "create", "edit", "rembg"], kinds)
cr = [x for x in lib if x["kind"] == "create"]
check("create metadata (aspect, seed, size)", all(x["aspect"] == "3:4" and x["seed"] is not None and x["w"] for x in cr), cr[:1])
check("files exist in output dir", all(os.path.exists(os.path.join(st()["settings"]["output_dir"], x["file"])) for x in lib))
c, b = req(UI + f"/thumbs/{lib[0]['id']}.jpg", raw=True)
check("thumbnail served", c == 200 and len(b) > 100, c)
gpus = {j.get("gpu") for j in st()["jobs"] if j["status"] == "done"}
check("jobs spread over both GPUs", gpus >= {0, 1}, gpus)

# 4) prompt helper
c, r = req(UI + "/api/improve", {"prompt": "a cat on a roof", "mode": "t2i", "aspect": "1:1"})
check("improve starts", c == 200 and r.get("id"), r)
j = wait(lambda: (lambda x: x if x.get("status") in ("done", "failed") else None)(req(UI + "/api/improve/" + r["id"])[1]), 60)
check("improve returns text without the thinking block", j and j.get("status") == "done" and j.get("text") and "<think>" not in j["text"], j)
c, r = req(UI + "/api/improve", {"prompt": "make it night", "mode": "i2i", "image": up["name"]})
j = wait(lambda: (lambda x: x if x.get("status") in ("done", "failed") else None)(req(UI + "/api/improve/" + r["id"])[1]), 60)
check("improve i2i (with photo) works", j and j.get("status") == "done", j)

# 5) the gateway API directly (what phone apps will use)
c, pair = req(UI + "/api/pairing")
check("pairing info (topic + key) for phone apps", c == 200 and pair.get("key") and pair.get("ntfy_topic"), pair)
GW, K = pair["url"], {"X-Key": pair["key"]}
check("gateway refuses a wrong key", req(GW + "/api/status", headers={"X-Key": "nope"})[0] == 401)
c, gs = req(GW + "/api/status", headers=K)
check("gateway status", c == 200 and gs["ready"] and "idle_s" in gs and "gpus" in gs, gs)
c, r = req(GW + "/api/jobs", {"kind": "create", "prompt": "sexy 15 year old", "source": "phone"}, headers=K)
check("gateway blocks minors + sexual (422)", c == 422 and r.get("blocked"), r)
c, r = req(GW + "/api/jobs", {"kind": "create", "prompt": "a kid flying a kite", "nsfw": True, "source": "phone"}, headers=K)
check("gateway blocks minor terms when NSFW is on", c == 422, r)
c, r = req(GW + "/api/jobs", {"kind": "edit", "prompt": "make her nude", "image": "missing.png", "source": "phone"}, headers=K)
check("gateway edit needs an uploaded image", c == 400, r)
c, r = req(GW + "/api/jobs", {"kind": "create", "prompt": "a lighthouse at dusk", "aspect": "16:9", "source": "phone"}, headers=K)
check("phone-style submit", c == 200 and r["jobs"][0]["params"]["w"] == 1344, r)
pj = r["jobs"][0]["id"]
j = wait(lambda: (lambda x: x if x.get("status") == "done" else None)(req(GW + f"/api/jobs/{pj}", headers=K)[1]), 60)
check("phone job finishes", j is not None, req(GW + f"/api/jobs/{pj}", headers=K)[1])
c, b = req(GW + f"/api/jobs/{pj}/image", headers=K, raw=True)
check("gateway serves the PNG", c == 200 and b[:4] == b"\x89PNG", c)
lib = wait(lambda: (lambda L: L if any(x.get("remote_id") == pj for x in L) else None)(req(UI + "/api/library")[1]), 30)
check("laptop also saves the phone's image", lib is not None and next(x for x in lib if x.get("remote_id") == pj)["from"] == "phone")

# 6) NSFW setting applies to Create (not Edit), cancel works
req(UI + "/api/settings", {"nsfw": True, "nsfw_strength": 0.8})
c, r = req(UI + "/api/submit", {"kind": "create", "prompt": "a woman in an evening dress, portrait", "count": 4})
check("create with NSFW on accepted", c == 200 and r.get("sent"), r)
c, r = req(UI + "/api/submit", {"kind": "create", "prompt": "children playing in a park"})
check("NSFW on + minor term blocked on laptop", c == 422, r)
c, r = req(UI + "/api/submit", {"kind": "edit", "prompt": "add a hat", "image": up["name"]})
time.sleep(1.5)
jobs = st()["jobs"]
nj = [j for j in jobs if j["kind"] == "create" and j["params"].get("nsfw")]
check("create jobs carry nsfw + strength", nj and all(abs(j["params"]["lora_strength"] - 0.8) < 1e-6 for j in nj), [j["params"] for j in nj][:1])
ej = [j for j in jobs if j["kind"] == "edit" and j["params"].get("prompt") == "add a hat"]
check("edit never carries nsfw", ej and not any(j["params"].get("nsfw") for j in ej), [j["params"] for j in ej])
q = [j for j in jobs if j["status"] == "queued" and not j.get("outbox")]
if q:
    c, r = req(UI + f"/api/jobs/{q[-1]['id']}/cancel", {})
    time.sleep(2.5)
    cj = next((j for j in st()["jobs"] if j["id"] == q[-1]["id"]), {})
    check("cancel a queued job", cj.get("status") == "cancelled", cj.get("status"))
else:
    check("cancel a queued job (none queued to cancel)", False)
req(UI + "/api/settings", {"nsfw": False})
wait(lambda: not any(j["status"] in ("queued", "running", "downloading") for j in st()["jobs"]), 90)

# 7) power off
req(UI + "/api/power", {"on": False})
s = wait(lambda: (lambda x: x if x["power"] == "off" else None)(st()), 40)
check("power off", s is not None)
time.sleep(1)
try:
    req(GW + "/api/status", headers=K)
    gone = False
except Exception:
    gone = True
check("kernel gone after power off", gone)

print(f"\n{len(OK)} passed, {len(FAIL)} failed", flush=True)
sys.exit(1 if FAIL else 0)

"""One live test through the app's own server (port 8766), the way the UI does it. Writes data/live_report.json.
Needs the server ready (power on). Does NOT power off (the caller always does that)."""
import json, os, sys, time, urllib.request

UI = "http://127.0.0.1:8766"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(HERE, "data", "live_report.json")
R = {"steps": []}


def req(path, body=None):
    r = urllib.request.Request(UI + path, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, **json.loads(e.read() or b"{}")}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def save():
    json.dump(R, open(REPORT, "w", encoding="utf-8"), indent=1)


def jobs():
    return req("/api/state")["jobs"]


def wait_jobs(ids, timeout):
    """Wait until the remote jobs created after `ids` are known finished and downloaded."""
    t = time.time()
    while time.time() - t < timeout:
        js = {j["id"]: j for j in jobs()}
        cur = [js.get(i) for i in ids]
        if all(c and c["status"] in ("done", "failed", "cancelled") for c in cur):
            return cur
        time.sleep(4)
    return [ {i: "timeout"} for i in ids]


def new_ids(before, kind):
    return [j["id"] for j in jobs() if j["id"] not in before and j["kind"] == kind and not j.get("outbox")]


def run(name, body, timeout=900):
    before = {j["id"] for j in jobs()}
    t0 = time.time()
    r = req("/api/submit", body)
    if r.get("http_error") or not r.get("sent"):
        R["steps"].append({"name": name, "error": r}); save(); log(name, "SUBMIT FAILED", r); return []
    time.sleep(2)
    ids = new_ids(before, body["kind"])
    res = wait_jobs(ids, timeout)
    lib = {x.get("remote_id"): x for x in req("/api/library")}
    out = []
    for j in res:
        if not isinstance(j, dict) or "id" not in j:
            out.append({"status": "timeout"}); continue
        x = lib.get(j["id"])
        out.append({"id": j["id"], "status": j["status"], "gpu": j.get("gpu"), "took": j.get("took"), "error": j.get("error"),
                    "started": j.get("started"), "finished": j.get("finished"), "note": j.get("note"),
                    "file": os.path.join(req("/api/state")["settings"]["output_dir"], x["file"]) if x else None,
                    "lib_id": x["id"] if x else None, "w": x.get("w") if x else None, "h": x.get("h") if x else None})
    step = {"name": name, "wall_s": round(time.time() - t0), "jobs": out}
    R["steps"].append(step); save()
    log(name, json.dumps(step)[:600])
    return out


s = req("/api/state")
R["start_state"] = {"power": s["power"], "live": s["live"], "ready_at": s["ready_at"], "pushed_at": s["pushed_at"]}
save()
if s["power"] != "ready":
    sys.exit("server not ready")

turbo = run("turbo_create_1x1", {"kind": "create", "prompt": "A Nepali grandmother making momos in a warm wooden kitchen, steam rising from a bamboo steamer, "
                                 "soft window light, photorealistic, 35mm photo", "aspect": "1:1", "quality": "turbo", "count": 1, "seed": 1234})
run("quality_create_3x4", {"kind": "create", "prompt": "A red vintage bicycle leaning against a blue wooden door in Bhaktapur, sunny afternoon, "
                           "carved window above, a small sign that reads \"CHIYA\", 35mm photo", "aspect": "3:4", "quality": "quality", "count": 1, "seed": 99})
if turbo and turbo[0].get("lib_id"):
    up = req(f"/api/library/{turbo[0]['lib_id']}/use", {})
    run("edit_night_snow", {"kind": "edit", "image": up["name"], "prompt": "make it night with snow falling", "count": 1, "seed": 5})
    up2 = req(f"/api/library/{turbo[0]['lib_id']}/use", {})
    run("remove_background", {"kind": "rembg", "image": up2["name"]}, timeout=1200)

# prompt helper (Create)
t0 = time.time()
r = req("/api/improve", {"prompt": "momo shop in Kathmandu at night", "mode": "t2i", "aspect": "1:1"})
imp = {"name": "improve_t2i", "start": r}
if r.get("id"):
    while time.time() - t0 < 900:
        j = req("/api/improve/" + r["id"])
        if j.get("status") in ("done", "failed", "cancelled"):
            imp.update(status=j["status"], took=j.get("took"), text=j.get("text"), error=j.get("error"), gpu=j.get("gpu"))
            break
        time.sleep(5)
    else:
        imp["status"] = "timeout"
imp["wall_s"] = round(time.time() - t0)
R["steps"].append(imp); save()
log("improve", json.dumps(imp)[:900])

run("two_turbo_parallel", {"kind": "create", "prompt": "A snow leopard resting on a rocky Himalayan ridge at sunrise, telephoto wildlife photo",
                           "aspect": "16:9", "quality": "turbo", "count": 2, "seed": 777})
R["end_state"] = req("/api/state")["live"]
save()
log("ALL DONE")

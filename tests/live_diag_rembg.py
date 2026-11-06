"""Diagnostic for background removal on the live server: runs variants as raw ComfyUI workflows on GPU 2 (/g1 passthrough).
Usage: <python> -B tests/live_diag_rembg.py IMAGE_PATH VARIANT...   variants: turbo | quality_rgb
Saves results to data/diag/."""
import json, os, sys, time, urllib.request, uuid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["QIS_TEST"] = "1"
sys.path.insert(0, os.path.join(HERE, "kaggle"))
import qis_server as k  # noqa: E402

pair = json.loads(urllib.request.urlopen("http://127.0.0.1:8766/api/pairing", timeout=20).read())
GW, KEY = pair["url"], pair["key"]
OUT = os.path.join(HERE, "data", "diag")
os.makedirs(OUT, exist_ok=True)


def call(path, body=None, raw=None, ctype=None, timeout=120):
    h = {"X-Key": KEY}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if raw is not None:
        data, h["Content-Type"] = raw, ctype
    r = urllib.request.Request(GW + path, data=data, headers=h, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.read()


src = sys.argv[1]
b = uuid.uuid4().hex
body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"diag.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + open(src, "rb").read() + f"\r\n--{b}--\r\n".encode()
name = json.loads(call("/api/upload", raw=body, ctype=f"multipart/form-data; boundary={b}"))["name"]
k.OI = json.loads(call("/g1/object_info", timeout=180))
print("uploaded", name, "object_info nodes", len(k.OI), flush=True)

for variant in sys.argv[2:]:
    p = {"image": name, "seed": 7, "prompt": k.REMBG_PROMPT}
    if variant == "turbo":
        wf = k.wf_edit(p, f"diag_{variant}")                 # turbo edit path with the rembg prompt
        wf["13"] = {"class_type": "SaveImageAdvanced", "inputs": {"images": ["12", 0], "filename_prefix": f"diag_{variant}"}}
    elif variant == "quality_rgb":
        wf = k.wf_edit(p, f"diag_{variant}", rembg=True)
        wf["13"] = {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": f"diag_{variant}"}}
    else:
        continue
    print(variant, "fill:", k.fill(k.OI, wf), flush=True)
    t = time.time()
    pid = json.loads(call("/g1/prompt", {"prompt": wf, "client_id": "diag"}))["prompt_id"]
    while time.time() - t < 600:
        h = json.loads(call(f"/g1/history/{pid}"))
        if pid in h:
            st = h[pid]["status"]
            imgs = [f for o in h[pid].get("outputs", {}).values() for f in o.get("images", [])]
            print(variant, st.get("status_str"), f"{time.time() - t:.0f}s", imgs, flush=True)
            for f in imgs:
                q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")})
                open(os.path.join(OUT, f["filename"]), "wb").write(call(f"/g1/view?{q}"))
            if st.get("status_str") == "error":
                print(json.dumps(st)[:1500])
            break
        time.sleep(3)

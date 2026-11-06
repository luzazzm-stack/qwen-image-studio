# Qwen-Image-2.1 smoke test on Kaggle 2x T4 (batch kernel, no tunnel).
# Plan A: diffusion model + VAE on cuda:0, text encoder on cuda:1 (ComfyUI-MultiGPU).
# Writes report.json, comfy.log and sample PNGs to /kaggle/working.
import json, os, subprocess, sys, time, urllib.request, urllib.error, glob, traceback

OUT = "/kaggle/working"
W = "/tmp/qis"
C = f"{W}/ComfyUI"
REPO = "Comfy-Org/Qwen-Image-2.1"
FILES = {
    "diffusion_models": ["qwen_image_2.1_turbo_int8_convrot.safetensors", "qwen_image_2.1_int8_convrot.safetensors"],
    "text_encoders": ["qwen3vl_8b_int8_convrot.safetensors"],
    "vae": ["qwen_image_2.1_vae_bf16.safetensors"],
}
R = {"t0": time.time(), "steps": [], "runs": [], "errors": []}


def log(msg):
    print(msg, flush=True)
    R["steps"].append([round(time.time() - R["t0"]), msg])
    save()


def save():
    json.dump(R, open(f"{OUT}/report.json", "w"), indent=1, default=str)


def sh(cmd):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"{cmd}\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    return p.stdout


def http(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def gpu_mem():
    return sh("nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader").strip().split("\n")


def setup():
    os.makedirs(W, exist_ok=True)
    log("gpus: " + " | ".join(gpu_mem()))
    log("torch: " + sh(f"{sys.executable} -c \"import torch;print(torch.__version__, torch.version.cuda, torch.cuda.get_device_capability(0))\"").strip())
    sh(f"git clone -q --depth 1 https://github.com/comfyanonymous/ComfyUI {C}")
    log("cloned; installing requirements")
    sh(f"pip install -q -r {C}/requirements.txt 'huggingface_hub[hf_xet]' 2>&1 | tail -3")
    log("requirements installed; downloading models")
    from huggingface_hub import hf_hub_download
    for sub, names in FILES.items():
        for n in names:
            t = time.time()
            p = hf_hub_download(REPO, f"{sub}/{n}", local_dir=f"{W}/hf")
            os.makedirs(f"{C}/models/{sub}", exist_ok=True)
            os.symlink(p, f"{C}/models/{sub}/{n}")
            log(f"downloaded {n} in {round(time.time()-t)} s")


def start_comfy(extra_env=None, dev=0, tag="v"):
    args = [sys.executable, "main.py", "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch",
            "--preview-method", "none", "--output-directory", f"{W}/out", "--cuda-device", str(dev)]
    env = {**os.environ, **(extra_env or {})}
    p = subprocess.Popen(args, cwd=C, env=env, stdout=open(f"{W}/comfy_{tag}.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(300):
        if p.poll() is not None:
            raise RuntimeError("ComfyUI exited at start")
        try:
            http("http://127.0.0.1:8188/system_stats", timeout=5)
            log(f"ComfyUI up ({tag})")
            return p
        except Exception:
            time.sleep(2)
    raise RuntimeError("ComfyUI did not start")


def node_info():
    oi = http("http://127.0.0.1:8188/object_info", timeout=120)
    R["nodes"] = {
        "multigpu": sorted(k for k in oi if "MultiGPU" in k)[:60],
        "qwen": sorted(k for k in oi if "qwen" in k.lower()),
        "latent": sorted(k for k in oi if "Latent" in k and ("Empty" in k)),
        "modelsampling": sorted(k for k in oi if k.startswith("ModelSampling")),
        "clip_types": oi.get("CLIPLoader", {}).get("input", {}).get("required", {}).get("type", [[]])[0],
    }
    for k in ("CLIPLoaderMultiGPU", "UNETLoaderMultiGPU", "VAELoaderMultiGPU", "TextEncodeQwenImage21", "QwenImage21Cache"):
        if k in oi:
            R["nodes"][k] = oi[k]["input"]
    save()
    return oi


def fill(oi, wf):
    """Fill any required widget a node still lacks with its default (or first combo option)."""
    for n in wf.values():
        spec = oi.get(n["class_type"], {}).get("input", {}).get("required", {})
        for k, v in spec.items():
            if k in n["inputs"]:
                continue
            t, opt = v[0], (v[1] if len(v) > 1 and isinstance(v[1], dict) else {})
            if "default" in opt:
                n["inputs"][k] = opt["default"]
            elif isinstance(t, list) and t:
                n["inputs"][k] = t[0]
    return wf


TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"


def build(oi, unet, turbo, w, h, prompt, seed, split=True):
    mg = split and "CLIPLoaderMultiGPU" in oi and "UNETLoaderMultiGPU" in oi
    if mg:
        wf = {"1": {"class_type": "UNETLoaderMultiGPU", "inputs": {"unet_name": unet, "weight_dtype": "default", "device": "cuda:0"}},
              "2": {"class_type": "CLIPLoaderMultiGPU", "inputs": {"clip_name": FILES["text_encoders"][0], "type": "qwen_image", "device": "cuda:1"}},
              "3": ({"class_type": "VAELoaderMultiGPU", "inputs": {"vae_name": FILES["vae"][0], "device": "cuda:0"}}
                    if "VAELoaderMultiGPU" in oi else {"class_type": "VAELoader", "inputs": {"vae_name": FILES["vae"][0]}})}
    else:
        wf = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
              "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": FILES["text_encoders"][0], "type": "qwen_image", "device": "default"}},
              "3": {"class_type": "VAELoader", "inputs": {"vae_name": FILES["vae"][0]}}}
    wf["4"] = {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["2", 0], "prompt": prompt, "negative_prompt": ""}}
    wf["5"] = {"class_type": "QwenImage21Cache", "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}}
    wf["6"] = {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}
    if turbo:
        wf["7"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
        wf["8"] = {"class_type": "CFGGuider", "inputs": {"model": ["5", 0], "positive": ["4", 0], "negative": ["4", 1], "cfg": 1.0}}
        wf["9"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
        wf["10"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": TURBO_SIGMAS}}
        wf["11"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["7", 0], "guider": ["8", 0], "sampler": ["9", 0],
                    "sigmas": ["10", 0], "latent_image": ["6", 0]}}
        lat = ["11", 0]
    else:
        wf["11"] = {"class_type": "KSampler", "inputs": {"model": ["5", 0], "positive": ["4", 0], "negative": ["4", 1],
                    "latent_image": ["6", 0], "seed": seed, "steps": 25, "cfg": 1.0, "sampler_name": "euler",
                    "scheduler": "simple", "denoise": 1.0}}
        lat = ["11", 0]
    wf["12"] = {"class_type": "VAEDecode", "inputs": {"samples": lat, "vae": ["3", 0]}}
    wf["13"] = {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": "smoke"}}
    return fill(oi, wf), {"multigpu": mg, "mode": "turbo" if turbo else "standard"}


def run(name, wf, meta, timeout=1800):
    t = time.time()
    try:
        pid = http("http://127.0.0.1:8188/prompt", {"prompt": wf, "client_id": "smoke"})["prompt_id"]
    except urllib.error.HTTPError as e:
        rec = {"name": name, **meta, "status": "rejected", "error": e.read().decode()[-3000:], "workflow": wf}
        R["runs"].append(rec); log(f"run {name}: rejected"); return rec
    while time.time() - t < timeout:
        try:
            h = http(f"http://127.0.0.1:8188/history/{pid}")
        except Exception:
            rec = {"name": name, **meta, "status": "crashed", "seconds": round(time.time() - t, 1)}
            R["runs"].append(rec); log(f"run {name}: crashed"); return rec
        if pid in h:
            st = h[pid].get("status", {})
            dt = round(time.time() - t, 1)
            rec = {"name": name, **meta, "seconds": dt, "status": st.get("status_str"), "gpu_mem": gpu_mem()}
            if st.get("status_str") == "error":
                rec["error"] = json.dumps(st)[-2500:]
            else:
                imgs = [i["filename"] for o in h[pid]["outputs"].values() for i in o.get("images", [])]
                if imgs:
                    from PIL import Image, ImageStat
                    src = f"{W}/out/{imgs[0]}"
                    im = Image.open(src).convert("RGB")
                    rec["image_mean"] = [round(x, 1) for x in ImageStat.Stat(im).mean]
                    rec["image_std"] = [round(x, 1) for x in ImageStat.Stat(im).stddev]
                    im.save(f"{OUT}/{name}.png")
            R["runs"].append(rec)
            log(f"run {name}: {rec.get('status')} {dt}s")
            return rec
        time.sleep(2)
    R["runs"].append({"name": name, "status": "timeout"})
    log(f"run {name}: timeout")


PROMPT = ("A cosy mountain teahouse at dawn in the Himalayas, prayer flags in the wind, warm light from the windows, "
          "a trekker drinking tea on the porch, a wooden sign that reads \"NAMASTE\", photorealistic, 35mm photo")

VARIANTS = [
    ("int8_default", {}),
    ("int8_no_cutlass", {"COMFY_KITCHEN_DISABLE_CUTLASS": "1"}),
    ("int8_fallback", {"COMFY_KITCHEN_DISABLE_CUTLASS": "1", "COMFY_KITCHEN_FORCE_INT4_INT8_FALLBACK": "1"}),
]
try:
    setup()
    turbo, std = FILES["diffusion_models"]
    oi = None
    for tag, env in VARIANTS:
        p = start_comfy(env, 0, tag)
        if oi is None:
            oi = node_info()
        wf, meta = build(oi, turbo, True, 1024, 1024, PROMPT, 42, split=False)
        meta["variant"] = tag
        r1 = run(f"{tag}_turbo_first", wf, meta)
        if r1 and r1.get("status") == "success":
            wf, meta = build(oi, turbo, True, 1024, 1024, PROMPT, 7, split=False); meta["variant"] = tag
            run(f"{tag}_turbo_warm", wf, meta)
            wf, meta = build(oi, std, False, 1024, 1024, PROMPT, 42, split=False); meta["variant"] = tag
            run(f"{tag}_standard_25steps", wf, meta)
            wf, meta = build(oi, turbo, True, 1024, 1024, PROMPT, 11, split=False); meta["variant"] = tag
            run(f"{tag}_turbo_after_standard", wf, meta)
            p.kill(); break
        p.kill(); time.sleep(5)
        try:
            sh(f"tail -c 6000 {W}/comfy_{tag}.log > {OUT}/comfy_{tag}.log")
        except Exception:
            pass
except Exception:
    R["errors"].append(traceback.format_exc())
    log("FAILED")
finally:
    R["total_s"] = round(time.time() - R["t0"])
    save()
    try:
        sh(f"for f in {W}/comfy_*.log; do tail -c 8000 $f > {OUT}/$(basename $f); done")
    except Exception:
        pass

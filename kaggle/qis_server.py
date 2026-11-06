# Qwen Image Studio server for Kaggle (2x T4). TEMPLATE: the app fills in the __PLACEHOLDERS__ and pushes it
# as the kernel. It runs Qwen-Image-2.1 in two ComfyUI instances (one per GPU), keeps the JOB QUEUE itself and
# serves a small key-protected JSON API (README.md -> "Gateway API") through a Cloudflare quick tunnel, so the
# Windows app and phone apps share one queue. The tunnel URL is announced on ntfy.sh/<TOPIC>.
# The script exits (ending the Kaggle session) when idle too long, at MAX_HOURS, or on POST /shutdown.
#
# Local test without Kaggle: QIS_TEST=1 (uses tools/fake_comfy.py instead of ComfyUI, no tunnel, no ntfy).
import asyncio, base64, json, os, random, re, subprocess, sys, threading, time, urllib.request, uuid

TEST = os.environ.get("QIS_TEST") == "1"
KEY, TOPIC = "__KEY__", "__TOPIC__"
IDLE_MIN, MAX_HOURS = "__IDLE_MIN__", "__MAX_HOURS__"
PE_SYSTEM_B64 = "__PE_SYSTEM_B64__"
NSFW_REPO, NSFW_FILE = "__NSFW_REPO__", "__NSFW_FILE__"      # optional adult-detail LoRA (set in the app's Settings)
if TEST:
    NSFW_REPO, NSFW_FILE = "test/lora", "NSFW Qwen Lora.safetensors"
    KEY, TOPIC = os.environ.get("QIS_KEY", "testkey"), "test"
    IDLE_MIN, MAX_HOURS = os.environ.get("QIS_IDLE_MIN", "30"), "2"
IDLE_MIN, MAX_HOURS = float(IDLE_MIN), float(MAX_HOURS)
if PE_SYSTEM_B64.startswith("__"):
    _pd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prompts")
    PE_SYSTEM = {m: open(os.path.join(_pd, f"pe_{m}_system.txt"), encoding="utf-8").read() for m in ("t2i", "i2i")}
else:
    PE_SYSTEM = json.loads(base64.b64decode(PE_SYSTEM_B64).decode("utf-8"))

W = os.environ.get("QIS_WORKDIR") or "/tmp/qis"
C = f"{W}/ComfyUI"
INPUT = f"{W}/input"                      # shared by both ComfyUIs (uploads)
PORTS = [int(p) for p in os.environ.get("QIS_PORTS", "8188,8189").split(",")]
GATEWAY_PORT = int(os.environ.get("QIS_GATEWAY_PORT", "8000"))
T0 = time.time()

REPO = "Comfy-Org/Qwen-Image-2.1"
TURBO_UNET = "qwen_image_2.1_turbo_int8_convrot.safetensors"
QUALITY_UNET = "qwen_image_2.1_int8_convrot.safetensors"
TE = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
# Prompt enhancer. int8 (9.47 GB each). If it misbehaves on T4, switch to the w4a8 files (6.72 GB): replace
# "int8_convrot" with "w4a8_convrot" here (w4a8 is untested on T4 / sm 7.5).
PE = {"t2i": "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors",
      "i2i": "qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors"}
PE_CLIP_TYPE = {"t2i": "stable_diffusion", "i2i": "qwen_image"}       # as in the official templates
CORE = [("diffusion_models", TURBO_UNET, 7.26), ("text_encoders", TE, 9.35), ("vae", VAE, 0.25),
        ("diffusion_models", QUALITY_UNET, 7.26)]
TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"
SIZES = {"1:1": (1024, 1024), "3:4": (896, 1184), "4:3": (1184, 896), "9:16": (768, 1344), "16:9": (1344, 768)}
REMBG_PROMPT = "Remove the background, and output a PNG image"
EDIT_RESOLUTION = 1024        # edit input is resized to about 1 MP (template uses 0 = own size; phone photos would not fit)
PE_MAX_LENGTH = 4096          # template value (official 24000); covers the thinking block + the prompt

NSFW_ON = bool(NSFW_REPO and not NSFW_REPO.startswith("__") and NSFW_FILE and not NSFW_FILE.startswith("__"))
NSFW_LORA = "qis_nsfw_lora.safetensors"     # local name in ComfyUI/models/loras
S = {"nsfw": "checking" if NSFW_ON else "off", "nsfw_detail": "", "stage": "booting", "pct": 0, "detail": "", "ready": False, "error": None, "timings": {},
     "gpus": [{} for _ in PORTS], "pe": {"t2i": False, "i2i": False}, "wf_check": None}
last_active = time.time()
shutdown = threading.Event()
procs, comfy, comfy_args = [], {}, {}
OI = {}                                    # live /object_info from ComfyUI
JOBS = []                                  # the shared queue (newest last)
gpu_model = {g: None for g in range(len(PORTS))}   # last diffusion model on each GPU (avoid swapping)
claimed = set()


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def ntfy(d):
    if TEST:
        return log("ntfy (test, not sent)", d)
    d = {**d, "t": time.time()}
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{TOPIC}", data=json.dumps(d).encode()), timeout=20)
    except Exception as e:
        log("ntfy failed", e)


def stage(name, pct, detail=""):
    S.update(stage=name, pct=pct, detail=detail)
    log("STAGE", name, pct, detail)


def sh(cmd):
    log("+", cmd)
    r = subprocess.run(cmd, shell=True)
    if r.returncode:
        raise RuntimeError(f"command failed ({r.returncode}): {cmd}")


def http_json(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


# ---------------------------------------------------------------- safety: no sexual content involving minors
# Enforced here in the gateway, so every client (Windows app, phone apps) is covered. Blocks a prompt, an edit
# instruction or an improved prompt that mentions a minor together with sexual/nude content (or any minor term
# while NSFW is switched on for that request).
_NUM_WORDS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen"
MINOR_RE = re.compile(
    r"\b(child|children|childlike|childish|kids?|kiddies?|minors?|teens?|teenage[rd]?s?|preteens?|pre-teens?|tweens?|underage[d]?|under-age"
    r"|school ?girls?|school ?boys?|lolis?|lolita|shota|young[- ]looking|youthful[- ]looking|little (girl|boy)s?|toddlers?|infants?"
    r"|juvenile|pubescent|prepubescent|jailbait|elementary school|middle school|high ?school(er)?s?|grade school"
    r"|keti|ketis|baccha|bacche|bacchi|bachha|bachhe|bachhi|bachcha|bachche|bachchi|nani|nanu|kishori|kishor|balika|balak|chhori)\b"
    r"|\b(1[0-7]|[1-9])\s*(yo|y/o|y\.o\.?|yrs?|years?)(\s*-?\s*old)?\b"
    r"|\b(1[0-7]|[1-9])[- ]years?[- ]old\b|\b(aged?|age of|age)\s*:?\s*(1[0-7]|[1-9])\b"
    r"|\b(" + _NUM_WORDS + r")[- ]years?[- ]old\b|\b(aged?|age of)\s*(" + _NUM_WORDS + r")\b", re.I)
SEXUAL_RE = re.compile(
    r"\b(nudes?|nudity|naked|nsfw|sex|sexy|sexual(ly)?|sexualis\w*|sexualiz\w*|erotic\w*|porn\w*|hentai|topless|bottomless|nipples?"
    r"|breasts?|boobs?|tits|genitals?|genitalia|vagina\w*|penis\w*|pussy|cock|lingerie|undress\w*|unclothed|strip(ping|tease|per)?"
    r"|explicit|seductive\w*|orgasm\w*|masturbat\w*|intercourse|aroused|lewd|fetish\w*|bdsm|bondage|spread legs|panties|thong"
    r"|bikini|underwear|cleavage|without clothes|no clothes|nangi|nanga|nango|nangai)\b", re.I)
REFUSAL = ("Blocked: this request combines minors with sexual or nude content. Qwen Image Studio never makes "
           "sexual images of anyone under 18. Remove the age or child references, or the sexual content.")


def safety_check(text, nsfw=False):
    """Returns the refusal message, or None if the text is allowed."""
    t = re.sub(r"[_*~`]+", " ", str(text or ""))
    if MINOR_RE.search(t) and (nsfw or SEXUAL_RE.search(t)):
        return REFUSAL
    return None


# ---------------------------------------------------------------- workflows (API format)
def fill(oi, wf):
    """Make each node match the live /object_info: fill required widgets with their defaults (also inside
    dynamic combos such as TextGenerate's sampling_mode) and drop inputs the node does not have.
    Returns a list of human-readable issues (empty = clean)."""
    issues = []
    for nid, n in wf.items():
        info = oi.get(n["class_type"])
        if not info:
            issues.append(f"{nid}: node {n['class_type']} is missing")
            continue
        allowed = set()

        def walk(spec, prefix, required):
            for k, v in (spec or {}).items():
                name = prefix + k
                t = v[0] if isinstance(v, (list, tuple)) and v else v
                opt = v[1] if isinstance(v, (list, tuple)) and len(v) > 1 and isinstance(v[1], dict) else {}
                allowed.add(name)
                if t == "COMFY_AUTOGROW_V3":
                    tpl = opt.get("template", {})
                    for nm in tpl.get("names", []) or [f"{k}{i}" for i in range(16)]:
                        allowed.add(f"{name}.{nm}")
                    continue
                if t == "COMFY_DYNAMICCOMBO_V3":
                    options = opt.get("options", [])
                    keys = [o.get("key") for o in options if isinstance(o, dict)]
                    if name not in n["inputs"] and required:
                        n["inputs"][name] = opt.get("default", keys[0] if keys else None)
                    chosen = next((o for o in options if isinstance(o, dict) and o.get("key") == n["inputs"].get(name)), None)
                    if chosen:
                        sub = chosen.get("inputs", {})
                        walk(sub.get("required", sub if "required" not in sub and "optional" not in sub else {}), name + ".", True)
                        walk(sub.get("optional", {}), name + ".", False)
                    continue
                if name in n["inputs"] or not required:
                    continue
                if "default" in opt:
                    n["inputs"][name] = opt["default"]
                elif isinstance(t, list) and t:
                    n["inputs"][name] = t[0]
                elif t == "COMBO" and opt.get("options"):
                    n["inputs"][name] = opt["options"][0]
                elif isinstance(t, str) and t.isupper() and t not in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"):
                    issues.append(f"{nid} {n['class_type']}: required input '{name}' ({t}) is not connected")
        walk(info.get("input", {}).get("required"), "", True)
        walk(info.get("input", {}).get("optional"), "", False)
        for k in list(n["inputs"]):
            if k not in allowed:
                v = n["inputs"].pop(k)
                issues.append(f"{nid} {n['class_type']}: dropped unknown input '{k}'={str(v)[:40]}")
    return issues


def wf_create(p, prefix):
    w, h = p["w"], p["h"]
    turbo = p.get("quality") != "quality"
    wf = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": TURBO_UNET if turbo else QUALITY_UNET, "weight_dtype": "default"}},
          "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TE, "type": "qwen_image", "device": "default"}},
          "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
          "4": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["2", 0], "prompt": p["prompt"], "negative_prompt": p.get("negative", "")}},
          "5": {"class_type": "QwenImage21Cache", "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}},
          "6": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}}}
    if p.get("nsfw") and (S["nsfw"] == "ok" or p.get("_force_lora")):
        # UNETLoader -> LoraLoaderModelOnly -> QwenImage21Cache
        wf["15"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": NSFW_LORA,
                                                                   "strength_model": float(p.get("lora_strength", 1.0))}}
        wf["5"]["inputs"]["model"] = ["15", 0]
    sampler(wf, turbo, p["seed"], ["6", 0], cfg=1.0)
    wf["12"] = {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}}
    wf["13"] = {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": prefix}}
    return wf


def sampler(wf, turbo, seed, latent, cfg=1.0, steps=25):
    if turbo:   # Qwen-Image-2.1-Turbo 8-step schedule (official template)
        wf["7"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
        wf["8"] = {"class_type": "CFGGuider", "inputs": {"model": ["5", 0], "positive": ["4", 0], "negative": ["4", 1], "cfg": cfg}}
        wf["9"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
        wf["10"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": TURBO_SIGMAS}}
        wf["11"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["7", 0], "guider": ["8", 0], "sampler": ["9", 0],
                                                                       "sigmas": ["10", 0], "latent_image": latent}}
    else:
        wf["11"] = {"class_type": "KSampler", "inputs": {"model": ["5", 0], "positive": ["4", 0], "negative": ["4", 1], "latent_image": latent,
                                                         "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler",
                                                         "scheduler": "simple", "denoise": 1.0}}


def wf_edit(p, prefix, rembg=False):
    """Turbo image edit (template image_qwen_image_2_1_turbo_image_edit) or background removal
    (template image_qwen_image_2_1_background_removal: quality model, 25 steps). Output size follows the photo."""
    turbo = not rembg
    wf = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": TURBO_UNET if turbo else QUALITY_UNET, "weight_dtype": "default"}},
          "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TE, "type": "qwen_image", "device": "default"}},
          "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
          "14": {"class_type": "LoadImage", "inputs": {"image": p["image"]}},
          "4": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "images.image_1": ["14", 0],
                                                                  "prompt": REMBG_PROMPT if rembg else p["prompt"],
                                                                  "negative_prompt": "", "resolution": EDIT_RESOLUTION}},
          "5": {"class_type": "QwenImage21Cache", "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}}}
    sampler(wf, turbo, p["seed"], ["4", 2], cfg=1.0)
    wf["12"] = {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}}
    if rembg and "SaveImageAdvanced" in OI:      # as in the template (PNG, keeps an alpha channel if the VAE gives one)
        wf["13"] = {"class_type": "SaveImageAdvanced", "inputs": {"images": ["12", 0], "filename_prefix": prefix}}
    else:
        wf["13"] = {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": prefix}}
    return wf


def wf_improve(p):
    """Official Qwen prompt enhancer (TextGenerate). mode t2i (Create) or i2i (Edit, sees the photo)."""
    mode = "i2i" if p.get("mode") == "i2i" else "t2i"
    user = p["prompt"].strip()
    if mode == "t2i" and p.get("aspect"):
        user += f" (aspect ratio {p['aspect']})"
    wf = {"1": {"class_type": "CLIPLoader", "inputs": {"clip_name": PE[mode], "type": PE_CLIP_TYPE[mode], "device": "default"}},
          "2": {"class_type": "TextGenerate", "inputs": {
              "clip": ["1", 0], "system_prompt": PE_SYSTEM[mode], "prompt": user, "max_length": PE_MAX_LENGTH,
              "thinking": True, "sampling_mode": "on", "sampling_mode.seed": p["seed"], "sampling_mode.temperature": 1.0,
              "sampling_mode.top_k": 20, "sampling_mode.top_p": 0.95, "sampling_mode.min_p": 0.0,
              "sampling_mode.repetition_penalty": 1.0, "sampling_mode.presence_penalty": 1.5 if mode == "t2i" else 0.0}},
          "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}}
    if mode == "i2i" and p.get("image"):
        wf["4"] = {"class_type": "LoadImage", "inputs": {"image": p["image"]}}
        wf["2"]["inputs"]["image"] = ["4", 0]
    return wf


def build(j):
    p, prefix = j["params"], f"qis_{j['id']}"
    k = j["kind"]
    wf = (wf_create(p, prefix) if k == "create" else wf_edit(p, prefix) if k == "edit"
          else wf_edit(p, prefix, rembg=True) if k == "rembg" else wf_improve(p))
    if OI:
        issues = fill(OI, wf)
        if issues:
            log("workflow", k, "adjusted:", "; ".join(issues)[:600])
    return wf


def job_model(j):
    if j["kind"] == "improve":
        return PE["i2i" if j["params"].get("mode") == "i2i" else "t2i"]
    if j["kind"] == "rembg" or (j["kind"] == "create" and j["params"].get("quality") == "quality"):
        return QUALITY_UNET
    return TURBO_UNET


def self_test():
    """Build one workflow of every kind and check it against the live /object_info; log every mismatch."""
    sample = {"create": {"prompt": "test", "w": 1024, "h": 1024, "quality": "turbo", "seed": 1},
              "create_nsfw": {"prompt": "test", "w": 1024, "h": 1024, "quality": "turbo", "seed": 1, "nsfw": True, "_force_lora": True},
              "create_quality": {"prompt": "test", "w": 1024, "h": 1024, "quality": "quality", "seed": 1},
              "edit": {"prompt": "make it night", "image": "x.png", "seed": 1}, "rembg": {"image": "x.png", "seed": 1},
              "improve_t2i": {"prompt": "a cat", "mode": "t2i", "seed": 1, "aspect": "1:1"},
              "improve_i2i": {"prompt": "make it night", "mode": "i2i", "image": "x.png", "seed": 1}}
    out = {}
    for name, p in sample.items():
        kind = name.split("_")[0]
        p = dict(p)
        wf = {"create": lambda: wf_create(p, "t"), "edit": lambda: wf_edit(p, "t"), "rembg": lambda: wf_edit(p, "t", True),
              "improve": lambda: wf_improve(p)}[kind]()
        issues = fill(OI, wf)
        for nid, n in wf.items():          # combo values (model file names) must exist; files still downloading are fine
            spec = {**OI.get(n["class_type"], {}).get("input", {}).get("required", {}), **OI.get(n["class_type"], {}).get("input", {}).get("optional", {})}
            for k, v in n["inputs"].items():
                s = spec.get(k)
                opts = s[0] if s and isinstance(s[0], list) else (s[1].get("options") if s and len(s) > 1 and isinstance(s[1], dict) and s[0] == "COMBO" else None)
                if opts and isinstance(v, str) and v not in opts and not (k == "image" and n["class_type"] == "LoadImage") and v not in PE.values() and v != NSFW_LORA:
                    issues.append(f"{nid} {n['class_type']}: {k}='{v}' is not one of the choices")
        out[name] = issues
        log("self-test", name, "OK" if not issues else "ISSUES: " + " | ".join(issues))
    S["wf_check"] = out


# ---------------------------------------------------------------- setup (runs in a thread)
def hf_get(sub, name):
    from huggingface_hub import hf_hub_download
    dest = f"{C}/models/{sub}/{name}"
    if os.path.exists(dest):
        return
    p = hf_hub_download(REPO, f"{sub}/{name}", local_dir=f"{W}/hf")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.symlink(p, dest)


def start_comfy(g):
    port = PORTS[g]
    if TEST:
        args = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "fake_comfy.py"),
                str(port), f"{W}/out{g}", INPUT]
        cwd = W
    else:
        args = [sys.executable, "main.py", "--listen", "127.0.0.1", "--port", str(port), "--cuda-device", str(g),
                "--output-directory", f"{W}/out{g}", "--input-directory", INPUT,
                "--disable-auto-launch", "--preview-method", "none"]
        cli = open(f"{C}/comfy/cli_args.py").read()
        # T4 has no bf16, so ComfyUI ran the VAE in fp16: normal images were fine, but the RGBA decode used by
        # "Remove background" overflowed (black 16-px blocks, alpha ~0). Live test 2026-11-05. fp32 VAE fixes the precision.
        if "--fp32-vae" in cli:
            args += ["--fp32-vae"]
        if "--user-directory" in cli:
            args += ["--user-directory", f"{W}/user{g}"]
        if "--database-url" in cli:
            args += ["--database-url", f"sqlite:///{W}/db{g}.sqlite"]
        cwd = C
        # Keep ComfyUI's default dynamic VRAM (no --disable-dynamic-vram): both instances share the model files via mmap.
    for d in (f"{W}/out{g}", INPUT, f"{W}/user{g}"):
        os.makedirs(d, exist_ok=True)
    comfy_args[g] = (args, cwd)
    comfy[g] = subprocess.Popen(args, cwd=cwd, stdout=open(f"{W}/comfy{g}.log", "w"), stderr=subprocess.STDOUT)
    procs.append(comfy[g])


def run_and_wait(port, wf, timeout=1200):
    pid = http_json(f"http://127.0.0.1:{port}/prompt", {"prompt": wf, "client_id": "warmup"})["prompt_id"]
    t = time.time()
    while time.time() - t < timeout:
        h = http_json(f"http://127.0.0.1:{port}/history/{pid}")
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                raise RuntimeError(f"warmup error on port {port}: {json.dumps(st)[:1500]}")
            return time.time() - t
        time.sleep(2)
    raise RuntimeError("warmup timeout")


def setup():
    global OI, last_active
    try:
        os.makedirs(W, exist_ok=True)
        if not TEST:
            stage("installing", 3, "ComfyUI")
            sh(f"git clone -q --depth 1 https://github.com/comfyanonymous/ComfyUI {C}")
            sh(f"pip install -q -r {C}/requirements.txt 'huggingface_hub[hf_xet]' 2>&1 | tail -3")
            total = sum(s for _, _, s in CORE)
            done = 0
            for sub, name, size in CORE:
                stage("downloading", 12 + int(58 * done / total), f"{name.split('_int8')[0].split('_bf16')[0]} ({size:.1f} GB)")
                t = time.time()
                hf_get(sub, name)
                done += size
                log(f"downloaded {name} in {time.time() - t:.0f} s")
            if NSFW_ON:
                stage("downloading", 70, "NSFW detail LoRA")
                try:
                    from huggingface_hub import hf_hub_download
                    lp = hf_hub_download(NSFW_REPO, NSFW_FILE, local_dir=f"{W}/hf_lora")
                    os.makedirs(f"{C}/models/loras", exist_ok=True)
                    os.symlink(lp, f"{C}/models/loras/{NSFW_LORA}")
                except Exception as e:
                    S.update(nsfw="unavailable", nsfw_detail=f"download failed: {e}"[:300])
                    log("NSFW LoRA download failed", e)
            S["timings"]["setup_s"] = round(time.time() - T0)
        stage("starting", 72, "launching ComfyUI on both GPUs")
        for g in range(len(PORTS)):
            start_comfy(g)
        for g, port in enumerate(PORTS):
            for _ in range(300):
                if comfy[g].poll() is not None:
                    raise RuntimeError(f"ComfyUI on GPU {g + 1} exited: " + open(f"{W}/comfy{g}.log").read()[-1500:])
                try:
                    http_json(f"http://127.0.0.1:{port}/system_stats", timeout=5)
                    break
                except Exception:
                    time.sleep(2)
            else:
                raise RuntimeError(f"ComfyUI on {port} did not start: " + open(f"{W}/comfy{g}.log").read()[-1500:])
        OI = http_json(f"http://127.0.0.1:{PORTS[0]}/object_info", timeout=180)
        self_test()

        stage("warming", 82, "loading the model into both GPUs")
        res = {}

        def warm(g, port):
            p = {"prompt": "a calm lake at sunrise", "w": 512, "h": 512, "quality": "turbo", "seed": 1}
            if g == len(PORTS) - 1 and S["nsfw"] == "checking":
                # LoRA self-test: apply it once (in the last GPU's warm-up) and check that ComfyUI could use it.
                logf = f"{W}/comfy{g}.log"
                start = os.path.getsize(logf) if os.path.exists(logf) else 0
                try:
                    wf = wf_create({**p, "nsfw": True, "_force_lora": True}, "warmup_lora")
                    issues = fill(OI, wf)
                    if "LoraLoaderModelOnly" not in OI or any("Lora" in i for i in issues):
                        raise RuntimeError("; ".join(issues) or "LoraLoaderModelOnly missing")
                    res[g] = run_and_wait(port, wf)
                    gpu_model[g] = TURBO_UNET
                    tail = open(logf, errors="replace").read()[start:] if os.path.exists(logf) else ""
                    missed = len(re.findall(r"lora key not loaded", tail, re.I))
                    if missed > 20:
                        S.update(nsfw="unavailable", nsfw_detail=f"the LoRA does not fit this model ({missed} keys not loaded)")
                    else:
                        S.update(nsfw="ok", nsfw_detail=f"applied ({missed} keys not loaded)")
                    log("NSFW LoRA self-test:", S["nsfw"], S["nsfw_detail"])
                    return
                except Exception as e:
                    S.update(nsfw="unavailable", nsfw_detail=f"failed: {e}"[:300])
                    log("NSFW LoRA self-test failed, warming up without it:", e)
            try:
                wf = wf_create(p, "warmup")
                fill(OI, wf)
                res[g] = run_and_wait(port, wf)
                gpu_model[g] = TURBO_UNET
            except Exception as e:
                res[g] = e
        ts = [threading.Thread(target=warm, args=(g, p)) for g, p in enumerate(PORTS)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        for g, r in res.items():
            if isinstance(r, Exception):
                raise r
            S["timings"][f"warm{g}_s"] = round(r)
        S["timings"]["ready_s"] = round(time.time() - T0)
        last_active = time.time()
        S["ready"] = True
        stage("ready", 100, "")
        ntfy({"type": "ready", "url": S.get("url")})
        threading.Thread(target=download_pe, daemon=True).start()
    except Exception as e:
        S["error"] = str(e)[:3000]
        stage("error", S["pct"], str(e)[:300])
        ntfy({"type": "error", "error": str(e)[:1000]})
        for g in range(len(PORTS)):
            p = f"{W}/comfy{g}.log"
            if os.path.exists(p):
                log(f"--- comfy{g}.log tail ---\n" + open(p).read()[-3000:])


def download_pe():
    """The prompt helper models download after the server is ready, so images can start sooner."""
    for mode in ("t2i", "i2i"):
        try:
            t = time.time()
            if not TEST:
                hf_get("text_encoders", PE[mode])
            S["pe"][mode] = True
            log(f"prompt helper {mode} ready in {time.time() - t:.0f} s")
        except Exception as e:
            log("prompt helper download failed", mode, e)
            S["pe"][mode] = "failed"


# ---------------------------------------------------------------- the queue
RUNNING = ("submitting", "running")
STATS = {"create_turbo": 26.0, "create_quality": 175.0, "edit": 75.0, "rembg": 300.0, "improve": 390.0}   # measured live 2026-11-05


def stat_key(j):
    return j["kind"] + ("_" + ("quality" if j["params"].get("quality") == "quality" else "turbo") if j["kind"] == "create" else "")


def estimate(j, g=None):
    p = j["params"]
    mp = p.get("w", 1024) * p.get("h", 1024) / 1048576 if j["kind"] == "create" else 1.0
    est = STATS[stat_key(j)] * (0.35 + 0.65 * mp if j["kind"] == "create" else 1)
    if g is not None and gpu_model.get(g) != job_model(j):
        est += 35     # loading a different model
    return int(est)


def public_job(j):
    d = {k: j.get(k) for k in ("id", "kind", "status", "gpu", "progress", "phase", "created", "started", "finished",
                               "error", "note", "took", "source", "text", "blocked")}
    d["params"] = {k: v for k, v in j["params"].items() if k != "system"}
    if j.get("result"):
        d["result"] = {k: j["result"][k] for k in ("w", "h", "bytes", "name") if k in j["result"]}
    if j["status"] in ("queued",) + RUNNING:
        d["estimate"] = estimate(j, j.get("gpu"))
        if j["status"] == "running" and j.get("started"):
            d["eta"] = max(3, int(d["estimate"] - (time.time() - j["started"])))
    return d


def new_job(kind, params, source=""):
    j = {"id": uuid.uuid4().hex[:10], "kind": kind, "status": "queued", "params": params, "created": time.time(),
         "gpu": None, "progress": 0, "source": source}
    JOBS.append(j)
    del JOBS[:-400]
    return j


def pick(g):
    """Prompt-helper jobs first (someone is waiting at the screen), then prefer a job that uses the model already on this GPU."""
    q = [j for j in JOBS if j["status"] == "queued" and j["id"] not in claimed]
    ready = [j for j in q if j["kind"] != "improve" or S["pe"].get("i2i" if j["params"].get("mode") == "i2i" else "t2i") is True]
    for j in q:
        if j["kind"] == "improve" and S["pe"].get("i2i" if j["params"].get("mode") == "i2i" else "t2i") == "failed":
            j.update(status="failed", error="The prompt helper model could not be downloaded", finished=time.time())
    imp = [j for j in ready if j["kind"] == "improve"]
    if imp:
        return imp[0]
    rest = [j for j in ready if j["kind"] != "improve"]
    if not rest:
        return None
    same = [j for j in rest[:4] if job_model(j) == gpu_model.get(g)]
    return (same or rest)[0]


async def submit(j, g, session):
    try:
        wf = build(j)
        async with session.post(f"http://127.0.0.1:{PORTS[g]}/prompt", json={"prompt": wf, "client_id": "qis"}) as r:
            d = await r.json(content_type=None)
            if r.status >= 400 or d.get("node_errors"):
                raise RuntimeError(json.dumps(d.get("node_errors") or d.get("error") or d)[:800])
        j.update(status="running", prompt_id=d["prompt_id"], started=time.time(), check_fails=0,
                 restarts_at_submit=S["gpus"][g].get("restarts", 0))
        gpu_model[g] = job_model(j)
        log("job", j["id"], j["kind"], "-> gpu", g)
    except Exception as e:
        j.update(status="failed", error=f"Could not start: {e}"[:800], finished=time.time())
        log("submit failed", j["id"], e)
    finally:
        claimed.discard(j["id"])


def lost_job(j, why):
    if j.get("retries", 0) < 1:
        j.update(status="queued", gpu=None, prompt_id=None, progress=0, phase=None, retries=j.get("retries", 0) + 1,
                 note=f"{why}, so it is being made again.")
    else:
        j.update(status="failed", error=f"{why} twice. Try again, or turn the server off and on.", finished=time.time())
    log("job lost", j["id"], why)


async def check_running(j, session):
    g = j["gpu"]
    gp = S["gpus"][g]
    if gp.get("restarts", 0) > j.get("restarts_at_submit", 0):
        return lost_job(j, "The GPU crashed while making this")
    if time.time() - j["started"] > 3 * estimate(j) + 300:
        try:
            async with session.post(f"http://127.0.0.1:{PORTS[g]}/interrupt", json={"prompt_id": j["prompt_id"]}) as r:
                await r.read()
        except Exception:
            pass
        return lost_job(j, "It was taking far longer than expected")
    if gp.get("prompt_id") == j["prompt_id"] and gp.get("max"):
        j["progress"] = round(gp["value"] / gp["max"], 3)
        j["phase"] = "working" if gp["value"] < gp["max"] else "finishing"
    async with session.get(f"http://127.0.0.1:{PORTS[g]}/history/{j['prompt_id']}") as r:
        h = await r.json(content_type=None)
    if j["prompt_id"] not in (h or {}):
        return
    item = h[j["prompt_id"]]
    st = item.get("status", {})
    took = round(time.time() - j["started"])
    if st.get("status_str") == "error" or not st.get("completed", True):
        if j.get("cancel"):
            j.update(status="cancelled", finished=time.time())
        else:
            msg = "; ".join(str(m[1].get("exception_message", "")) for m in st.get("messages", []) if m[0] == "execution_error")
            j.update(status="failed", error=(msg or "Generation failed")[:800], finished=time.time())
        return
    if j["kind"] == "improve":
        texts = [t for o in item.get("outputs", {}).values() for t in (o.get("text") or []) if isinstance(t, str)]
        text = (texts[-1] if texts else "").strip()
        text = re.sub(r"(?s)^.*</think>", "", text).strip().strip('"').strip()
        if text and safety_check(text, j["params"].get("nsfw")):
            j.update(status="failed", error=REFUSAL, finished=time.time(), blocked=True)
        elif not text:
            j.update(status="failed", error="The prompt helper gave no answer (it may have run out of room). Try a shorter idea.", finished=time.time())
        else:
            j.update(status="done", text=text, took=took, finished=time.time(), progress=1)
    else:
        img = next((f for o in item.get("outputs", {}).values() for f in o.get("images", [])
                    if isinstance(f, dict) and f.get("type", "output") == "output"), None)
        if not img:
            j.update(status="failed", error="Finished but no image was made", finished=time.time())
            return
        path = os.path.join(f"{W}/out{g}", img.get("subfolder", ""), img["filename"])
        res = {"path": path, "name": img["filename"], "bytes": os.path.getsize(path) if os.path.exists(path) else 0}
        try:
            with open(path, "rb") as f:
                head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                res["w"], res["h"] = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        except Exception:
            pass
        j.update(status="done", result=res, took=took, finished=time.time(), progress=1)
    k = stat_key(j)
    base = took - (35 if j.get("cold") else 0)
    STATS[k] = round(STATS[k] * 0.7 + max(5, base) * 0.3, 1)
    log("job done", j["id"], j["kind"], f"{took}s")


def remove_job(j):
    """Forget a finished job and delete its image from the server's disk."""
    path = (j.get("result") or {}).get("path")
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            log("could not delete", path, e)
    if j in JOBS:
        JOBS.remove(j)


async def scheduler(session):
    while True:
        await asyncio.sleep(1)
        if not S["ready"]:
            continue
        try:
            for j in [j for j in JOBS if j["status"] == "running"]:
                try:
                    await check_running(j, session)
                except Exception as e:
                    j["check_fails"] = j.get("check_fails", 0) + 1
                    if j["check_fails"] > 300:
                        lost_job(j, "The GPU stopped responding")
            for j in [j for j in JOBS if j.get("delete") and j["status"] not in ("queued",) + RUNNING]:
                remove_job(j)          # deleted while it was running: gone now that its GPU stopped it
            busy = {j["gpu"] for j in JOBS if j["status"] in RUNNING}
            for g in range(len(PORTS)):
                if g in busy:
                    continue
                j = pick(g)
                if not j:
                    continue
                claimed.add(j["id"])
                j.update(status="submitting", gpu=g, progress=0, note=None, error=None, cold=gpu_model.get(g) != job_model(j))
                busy.add(g)
                asyncio.create_task(submit(j, g, session))   # claimed first, so the other GPU can't take the same job
        except Exception as e:
            log("scheduler error", repr(e))


# ---------------------------------------------------------------- gateway (JSON API, header X-Key)
async def gateway():
    import aiohttp
    from aiohttp import web

    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600))

    @web.middleware
    async def auth(req, handler):
        global last_active
        if (req.headers.get("X-Key") or req.query.get("key")) != KEY:
            return web.json_response({"error": "bad key"}, status=401)
        if req.method in ("POST", "DELETE") and req.path != "/shutdown":
            last_active = time.time()   # submits, uploads, cancel, delete. Polling and image downloads do not keep the GPUs on.
        return await handler(req)

    def status_body():
        return {**S, "uptime": round(time.time() - T0), "idle_s": round(time.time() - last_active),
                "idle_limit_s": IDLE_MIN * 60, "max_s": MAX_HOURS * 3600, "sizes": SIZES,
                "queue": sum(1 for j in JOBS if j["status"] == "queued")}

    async def health(req):
        return web.json_response(status_body())

    async def upload(req):
        reader = await req.multipart()
        part = await reader.next()
        ext = os.path.splitext(part.filename or "")[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            return web.json_response({"error": "Use a PNG, JPG or WEBP image"}, status=400)
        name = f"up_{uuid.uuid4().hex[:10]}{ext}"
        size = 0
        with open(os.path.join(INPUT, name), "wb") as f:
            while True:
                chunk = await part.read_chunk(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > 40 * 1024 ** 2:
                    return web.json_response({"error": "Image larger than 40 MB"}, status=400)
                f.write(chunk)
        return web.json_response({"name": name, "bytes": size})

    async def submit_jobs(req):
        d = await req.json()
        kind = d.get("kind")
        src = str(d.get("source") or "")[:40]
        seed = int(d.get("seed", -1) if str(d.get("seed", "")).lstrip("-").isdigit() else -1)
        rnd = lambda i: seed + i if seed >= 0 else random.randint(0, 2 ** 31)
        img = os.path.basename(d.get("image") or "")
        if kind in ("edit", "rembg") or (kind == "improve" and d.get("mode") == "i2i" and img):
            if not img or not os.path.exists(os.path.join(INPUT, img)):
                return web.json_response({"error": "Upload the image first (POST /api/upload)"}, status=400)
        prompt = (d.get("prompt") or "").strip()
        if kind in ("create", "edit", "improve") and not prompt:
            return web.json_response({"error": "Write a prompt first"}, status=400)
        # The NSFW LoRA is only for Create (text-to-image). Photo edits never use it: they usually show real people.
        nsfw = bool(d.get("nsfw")) and kind in ("create", "improve")
        refusal = safety_check(prompt, nsfw)
        if refusal:
            log("blocked a request (minors safety)")
            return web.json_response({"error": refusal, "blocked": True}, status=422)
        try:
            strength = max(0.0, min(2.0, float(d.get("lora_strength", 1.0))))
        except (TypeError, ValueError):
            strength = 1.0
        if kind == "create":
            aspect = d.get("aspect") if d.get("aspect") in SIZES else "1:1"
            w, h = SIZES[aspect]
            base = {"prompt": prompt[:4000], "negative": str(d.get("negative") or "")[:2000], "aspect": aspect, "w": w, "h": h,
                    "quality": "quality" if d.get("quality") == "quality" else "turbo",
                    "nsfw": nsfw, "lora_strength": strength}
            n = max(1, min(4, int(d.get("count", 1))))
            made = [new_job("create", {**base, "seed": rnd(i)}, src) for i in range(n)]
        elif kind == "edit":
            made = [new_job("edit", {"prompt": prompt[:4000], "image": img, "seed": rnd(i)}, src)
                    for i in range(max(1, min(4, int(d.get("count", 1)))))]
        elif kind == "rembg":
            made = [new_job("rembg", {"image": img, "seed": rnd(0)}, src)]
        elif kind == "improve":
            mode = "i2i" if d.get("mode") == "i2i" else "t2i"
            made = [new_job("improve", {"prompt": prompt[:2000], "mode": mode, "image": img if mode == "i2i" else None,
                                        "aspect": d.get("aspect") if d.get("aspect") in SIZES else None, "seed": rnd(0),
                                        "nsfw": nsfw}, src)]
        else:
            return web.json_response({"error": "kind must be create, edit, rembg or improve"}, status=400)
        if nsfw and kind == "create" and S["nsfw"] != "ok":
            for j in made:
                j["note"] = "NSFW detail unavailable on this server, so it is made without it."
        return web.json_response({"jobs": [public_job(j) for j in made]})

    async def list_jobs(req):
        since = float(req.query.get("since") or 0)
        return web.json_response({"jobs": [public_job(j) for j in JOBS if max(j["created"], j.get("finished") or 0, j.get("started") or 0) >= since
                                           or j["status"] in ("queued",) + RUNNING],
                                  "status": {k: v for k, v in status_body().items() if k != "wf_check"}, "now": time.time()})

    def find(req):
        return next((j for j in JOBS if j["id"] == req.match_info["id"]), None)

    async def get_job(req):
        j = find(req)
        return web.json_response(public_job(j)) if j else web.json_response({"error": "unknown job"}, status=404)

    async def job_image(req):
        j = find(req)
        if not j or not j.get("result") or not os.path.exists(j["result"]["path"]):
            return web.json_response({"error": "no image"}, status=404)
        return web.FileResponse(j["result"]["path"], headers={"Content-Type": "image/png"})

    async def cancel(req):
        j = find(req)
        if not j:
            return web.json_response({"error": "unknown job"}, status=404)
        if j["status"] == "queued":
            j.update(status="cancelled", finished=time.time())
        elif j["status"] in RUNNING:
            j["cancel"] = True
            try:
                async with session.post(f"http://127.0.0.1:{PORTS[j['gpu']]}/interrupt", json={"prompt_id": j.get("prompt_id")}) as r:
                    await r.read()
            except Exception as e:
                log("interrupt failed", e)
        return web.json_response(public_job(j))

    async def delete_job(req):
        """Removes a job and its result file (used by "Hide"/"Delete" in the apps). A queued or running job is cancelled
        first and removed as soon as its GPU stops it."""
        j = find(req)
        if not j:
            return web.json_response({"ok": True, "deleted": False})
        if j["status"] in ("queued",) + RUNNING:
            j["delete"] = True
            if j["status"] == "queued":
                j.update(status="cancelled", finished=time.time())
            else:
                j["cancel"] = True
                try:
                    async with session.post(f"http://127.0.0.1:{PORTS[j['gpu']]}/interrupt", json={"prompt_id": j.get("prompt_id")}) as r:
                        await r.read()
                except Exception as e:
                    log("interrupt failed", e)
                return web.json_response({"ok": True, "deleted": False, "pending": True})
        remove_job(j)
        return web.json_response({"ok": True, "deleted": True})

    async def do_shutdown(req):
        shutdown.set()
        return web.json_response({"ok": True})

    async def proxy(req):      # raw ComfyUI access (debugging, /object_info)
        g = int(req.match_info["g"])
        q = {k: v for k, v in req.query.items() if k != "key"}
        hdr = {"Content-Type": req.headers["Content-Type"]} if "Content-Type" in req.headers else {}
        async with session.request(req.method, f"http://127.0.0.1:{PORTS[g]}/{req.match_info['path']}",
                                   params=q, data=await req.read(), headers=hdr) as r:
            body = await r.read()
            return web.Response(body=body, status=r.status,
                                headers={"Content-Type": r.headers.get("Content-Type", "application/octet-stream")})

    async def ws_watch(g):
        while True:
            try:
                async with session.ws_connect(f"http://127.0.0.1:{PORTS[g]}/ws?clientId=qis") as ws:
                    async for m in ws:
                        if m.type != aiohttp.WSMsgType.TEXT:
                            continue
                        j = json.loads(m.data)
                        t, d = j.get("type"), j.get("data", {})
                        st = S["gpus"][g]
                        if t == "execution_start":
                            st.update(prompt_id=d.get("prompt_id"), value=0, max=0, node=None)
                        elif t == "executing" and d.get("node") is None:
                            st.update(prompt_id=None, value=0, max=0)
                        elif t == "progress":
                            st.update(value=d.get("value"), max=d.get("max"), node=d.get("node"), prompt_id=d.get("prompt_id"))
            except Exception:
                pass
            await asyncio.sleep(3)

    app = web.Application(client_max_size=64 * 1024 ** 2, middlewares=[auth])
    app.add_routes([web.get("/health", health), web.get("/api/status", health), web.post("/shutdown", do_shutdown),
                    web.post("/api/upload", upload), web.post("/api/jobs", submit_jobs), web.get("/api/jobs", list_jobs),
                    web.get("/api/jobs/{id}", get_job), web.get("/api/jobs/{id}/image", job_image),
                    web.post("/api/jobs/{id}/cancel", cancel),
                    web.delete("/api/jobs/{id}", delete_job), web.post("/api/jobs/{id}/delete", delete_job),
                    web.route("*", "/g{g:[01]}/{path:.*}", proxy)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1" if not TEST else "127.0.0.1", GATEWAY_PORT).start()
    log(f"gateway on :{GATEWAY_PORT}")

    async def start_background():
        while not S["ready"]:
            await asyncio.sleep(2)
        for g in range(len(PORTS)):
            asyncio.create_task(ws_watch(g))
        asyncio.create_task(scheduler(session))
    asyncio.create_task(start_background())


def tunnel():
    if TEST:
        S["url"] = f"http://127.0.0.1:{GATEWAY_PORT}"
        return
    sh("curl -fsSL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x /usr/local/bin/cloudflared")
    p = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{GATEWAY_PORT}"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    procs.append(p)
    for line in p.stdout:
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            S["url"] = m.group(0)
            log("URL", S["url"])
            ntfy({"type": "url", "url": S["url"]})
            break
    for _ in p.stdout:
        pass


def supervise():
    """Restart a ComfyUI that died (e.g. out of memory) so one crash doesn't take a GPU out for the session."""
    for g, p in list(comfy.items()):
        if p.poll() is None:
            continue
        tail = open(f"{W}/comfy{g}.log", errors="replace").read()[-2500:]
        log(f"ComfyUI on GPU {g} exited with {p.returncode}; restarting. Log tail:\n{tail}")
        st = S["gpus"][g]
        st["restarts"] = st.get("restarts", 0) + 1
        st["last_crash"] = tail[-600:]
        st.update(prompt_id=None, value=0, max=0)
        gpu_model[g] = None
        args, cwd = comfy_args[g]
        comfy[g] = subprocess.Popen(args, cwd=cwd, stdout=open(f"{W}/comfy{g}.log", "a"), stderr=subprocess.STDOUT)
        procs.append(comfy[g])


def busy():
    return any(j["status"] in ("queued",) + RUNNING for j in JOBS)


async def main():
    global last_active
    if not TEST:
        sh("pip install -q aiohttp 2>&1 | tail -1")
    await gateway()
    threading.Thread(target=tunnel, daemon=True).start()
    threading.Thread(target=setup, daemon=True).start()
    reason = None
    while not reason:
        await asyncio.sleep(5 if TEST else 15)
        if S["ready"]:
            supervise()
        if busy():
            last_active = time.time()
        if shutdown.is_set():
            reason = "stopped from app"
        elif time.time() - last_active > IDLE_MIN * 60 and (S["ready"] or S["error"]):
            reason = f"idle for {IDLE_MIN:g} min"
        elif time.time() - T0 > MAX_HOURS * 3600:
            reason = f"reached {MAX_HOURS:g} h limit"
    log("EXIT:", reason)
    ntfy({"type": "stopped", "reason": reason})
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())

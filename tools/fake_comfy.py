"""A tiny stand-in for ComfyUI, used only for local tests (QIS_TEST=1). No GPU, no models.
It answers /system_stats, /object_info, /prompt, /history, /interrupt, /free and writes plain PNGs.
Its /object_info is hand-written from the smoke test report and the official templates: it proves the
plumbing, not that the real ComfyUI accepts the workflows (the kernel's self-test checks that on Kaggle).
Usage: python fake_comfy.py PORT OUTPUT_DIR INPUT_DIR"""
import json, os, struct, sys, threading, time, uuid, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, OUT, INP = int(sys.argv[1]), sys.argv[2], sys.argv[3]
DELAY = float(os.environ.get("FAKE_DELAY", "3"))
os.makedirs(OUT, exist_ok=True)

FILES = {"diffusion_models": ["qwen_image_2.1_turbo_int8_convrot.safetensors", "qwen_image_2.1_int8_convrot.safetensors"],
         "text_encoders": ["qwen3vl_8b_int8_convrot.safetensors", "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors",
                           "qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors"],
         "vae": ["qwen_image_2.1_vae_bf16.safetensors"]}
I = lambda d=0, **k: ["INT", {"default": d, **k}]
F = lambda d=0.0: ["FLOAT", {"default": d}]
STR = ["STRING", {"multiline": True}]
CLIP_TYPES = ["stable_diffusion", "wan", "qwen_image", "flux2"]
SAMPLING_ON = {"required": {"temperature": F(0.7), "top_k": I(64), "top_p": F(0.95), "min_p": F(0.05),
                            "repetition_penalty": F(1.05), "presence_penalty": F(0.0), "seed": I(0)}}
OI = {
    "UNETLoader": {"required": {"unet_name": [FILES["diffusion_models"]], "weight_dtype": [["default", "fp8_e4m3fn"]]}},
    "CLIPLoader": {"required": {"clip_name": [FILES["text_encoders"]], "type": [CLIP_TYPES]}, "optional": {"device": [["default", "cpu"]]}},
    "VAELoader": {"required": {"vae_name": [FILES["vae"]]}},
    "LoraLoaderModelOnly": {"required": {"model": ["MODEL", {}], "lora_name": [["qis_nsfw_lora.safetensors"]], "strength_model": F(1.0)}},
    "TextEncodeQwenImage21": {"required": {"clip": ["CLIP", {}], "prompt": STR, "negative_prompt": STR, "resolution": I(1024),
                                           "images": ["COMFY_AUTOGROW_V3", {"template": {"input": {"required": {"image": ["IMAGE", {}]}},
                                                                                         "names": [f"image_{i}" for i in range(1, 17)], "min": 0}}]},
                              "optional": {"vae": ["VAE", {}]}},
    "QwenImage21Cache": {"required": {"model": ["MODEL", {}], "device": ["COMBO", {"default": "auto", "options": ["auto", "gpu", "cpu", "off"]}],
                                      "dtype": ["COMBO", {"default": "default", "options": ["default", "int8", "int4"]}]}},
    "EmptyLatentImage": {"required": {"width": I(512), "height": I(512), "batch_size": I(1)}},
    "RandomNoise": {"required": {"noise_seed": I(0)}},
    "CFGGuider": {"required": {"model": ["MODEL", {}], "positive": ["CONDITIONING", {}], "negative": ["CONDITIONING", {}], "cfg": F(8.0)}},
    "KSamplerSelect": {"required": {"sampler_name": [["euler", "dpmpp_2m"]]}},
    "ManualSigmas": {"required": {"sigmas": ["STRING", {"default": "1, 0.5, 0"}]}},
    "SamplerCustomAdvanced": {"required": {"noise": ["NOISE", {}], "guider": ["GUIDER", {}], "sampler": ["SAMPLER", {}],
                                           "sigmas": ["SIGMAS", {}], "latent_image": ["LATENT", {}]}},
    "KSampler": {"required": {"model": ["MODEL", {}], "seed": I(0), "steps": I(20), "cfg": F(8.0), "sampler_name": [["euler", "dpmpp_2m"]],
                              "scheduler": [["simple", "normal"]], "positive": ["CONDITIONING", {}], "negative": ["CONDITIONING", {}],
                              "latent_image": ["LATENT", {}], "denoise": F(1.0)}},
    "VAEDecode": {"required": {"samples": ["LATENT", {}], "vae": ["VAE", {}]}},
    "SaveImage": {"required": {"images": ["IMAGE", {}], "filename_prefix": ["STRING", {"default": "ComfyUI"}]}},
    "SaveImageAdvanced": {"required": {"images": ["IMAGE", {}], "filename_prefix": ["STRING", {"default": "ComfyUI"}],
                                       "format": [["png", "webp"]], "bit_depth": [["8-bit", "16-bit"]], "color_space": [["sRGB"]]}},
    "LoadImage": {"required": {"image": [[], {"image_upload": True}]}},
    "TextGenerate": {"required": {"clip": ["CLIP", {}], "prompt": STR, "max_length": I(256),
                                  "sampling_mode": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "on", "inputs": SAMPLING_ON},
                                                                                          {"key": "off", "inputs": {"required": {}}}]}],
                                  "thinking": ["BOOLEAN", {"default": False}]},
                     "optional": {"image": ["IMAGE", {}], "video": ["IMAGE", {}], "audio": ["AUDIO", {}], "system_prompt": ["STRING", {"forceInput": True}]}},
    "PreviewAny": {"required": {"source": ["*", {}]}},
}
OBJECT_INFO = {k: {"input": v, "output": [], "name": k} for k, v in OI.items()}
OUTPUT_NODES = {"SaveImage", "SaveImageAdvanced", "PreviewAny"}

history, lock, interrupted = {}, threading.Lock(), set()


def png(w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def validate(wf):
    errors = {}
    for nid, n in wf.items():
        spec = OI.get(n.get("class_type"))
        if not spec:
            errors[nid] = {"errors": [{"message": f"unknown node {n.get('class_type')}"}]}
            continue
        for k in spec.get("required", {}):
            if k not in n["inputs"] and spec["required"][k][0] not in ("COMFY_AUTOGROW_V3",):
                errors[nid] = {"errors": [{"message": f"missing {k}"}]}
    return errors


def run(pid, wf):
    time.sleep(DELAY)
    outputs = {}
    w = h = 256
    for n in wf.values():
        if n["class_type"] == "EmptyLatentImage":
            w, h = int(n["inputs"]["width"]), int(n["inputs"]["height"])
        if n["class_type"] == "TextEncodeQwenImage21" and "images.image_1" in n["inputs"]:
            w, h = 1024, 768
    for nid, n in wf.items():
        if n["class_type"] in ("SaveImage", "SaveImageAdvanced"):
            name = f"{n['inputs'].get('filename_prefix', 'img')}_00001_.png"
            with open(os.path.join(OUT, name), "wb") as f:
                f.write(png(w // 4, h // 4, (40 + int(nid) * 9 % 200, 120, 200)))
            outputs[nid] = {"images": [{"filename": name, "subfolder": "", "type": "output"}]}
        if n["class_type"] == "PreviewAny":
            outputs[nid] = {"text": ["<think>planning</think>A photorealistic improved prompt written by the fake prompt helper."]}
    ok = pid not in interrupted
    with lock:
        history[pid] = {"status": {"status_str": "success" if ok else "error", "completed": ok, "messages": []}, "outputs": outputs if ok else {}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/system_stats":
            return self.send({"system": {"comfyui_version": "fake"}})
        if p == "/object_info":
            return self.send(OBJECT_INFO)
        if p.startswith("/history/"):
            pid = p.split("/")[-1]
            with lock:
                return self.send({pid: history[pid]} if pid in history else {})
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        p = self.path.split("?")[0]
        if p == "/prompt":
            wf = json.loads(body)["prompt"]
            errs = validate(wf)
            if errs:
                return self.send({"error": {"message": "invalid prompt"}, "node_errors": errs}, 400)
            pid = uuid.uuid4().hex
            threading.Thread(target=run, args=(pid, wf), daemon=True).start()
            return self.send({"prompt_id": pid, "number": 1, "node_errors": {}})
        if p == "/interrupt":
            try:
                interrupted.add(json.loads(body or b"{}").get("prompt_id"))
            except Exception:
                pass
            return self.send({})
        self.send({})


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

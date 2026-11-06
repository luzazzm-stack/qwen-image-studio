# Qwen Image Studio

Make and edit pictures with **Qwen-Image-2.1** running on your free Kaggle GPUs (2× T4, account `leomaharjan`).
Same look and habits as Wan Studio.

## Start
Double-click **Qwen Image Studio** on the desktop (or the Start menu). Press the power button in the dock to turn the
Kaggle server on: about **5–7 minutes** the first time each session. You can queue images before it is ready.

- **Create**: describe the image, pick a shape (1:1, 3:4, 4:3, 9:16, 16:9, about 1 megapixel each), Turbo (~25 s) or
  Quality (~3 min), 1–4 images. The two GPUs share the work. Advanced: seed and things to avoid.
- **Edit**: drop a photo, write what should change (Turbo edit, ~75 s), or press **Remove background** (~5 min; see Known issues).
- **Improve my prompt**: the official Qwen prompt enhancer rewrites your idea on a free GPU (about 6 min on a T4; the server must be on).
- **My images**: saved in `Pictures\Qwen Image Studio` with prompt and settings; reuse, make more, edit, remove background, download, delete.
- **Phone**: same Wi-Fi, open the address in **Settings → Open on your phone** and type the PIN.
  If the phone can't connect, allow the port once in an **administrator** PowerShell:
  `netsh advfirewall firewall add rule name="Qwen Image Studio (port 8766, local Wi-Fi only)" dir=in action=allow protocol=TCP localport=8766 profile=private`

**Licence:** the Qwen-Image-2.1 model is for research and non-commercial use only.

## How it works
```
Windows app (app_main.py: window + server.py on :8766)        phone browser (PIN)
        |  power on: kaggle_ctl.push()  ->  Kaggle kernel leomaharjan/qwen-image-studio-server
        |  finds the server's address on ntfy.sh/<topic>
        v
Kaggle gateway (kaggle/qis_server.py, Cloudflare quick tunnel, header X-Key)
   JOB QUEUE + results  ->  ComfyUI on GPU 1 (:8188)  and  ComfyUI on GPU 2 (:8189)
```
- The **queue lives on the Kaggle gateway**, so the Windows app and future phone apps share one queue. The Windows app
  sends jobs, shows the queue, and downloads every finished image (including ones made from a phone) into the library.
  Jobs made while the server is off wait in the app's outbox and are sent when it is ready.
- The kernel installs ComfyUI, downloads the models from `Comfy-Org/Qwen-Image-2.1` (int8 files, ~24 GB, ~2–3 min),
  starts one ComfyUI per GPU, runs a **self-test** (every workflow is checked against the live `/object_info`; results
  are in the log and in `/health → wf_check`), warms both GPUs, then downloads the prompt-enhancer models (2 × 9.5 GB)
  in the background.
- Power off = `POST /shutdown`, then the kernel is deleted so the GPU session is surely gone. The kernel also stops
  itself after `idle minutes + 5` without use or at the max session hours, even if the laptop is off.
- Workflows (API format) are built in `kaggle/qis_server.py` from the official templates in `templates/`:
  Turbo = 8-step ManualSigmas + euler, CFG 1; Quality = KSampler 25 steps euler/simple CFG 1; Edit = turbo edit
  template (image resized to ~1 MP); Remove background = background-removal template (Quality model, 25 steps);
  Improve = `TextGenerate` with the template's system prompts (`prompts/`).

## NSFW switch and the minors safety block
- **Settings → NSFW detail** (off by default). When on, every new **Create** image gets a `LoraLoaderModelOnly`
  (strength 1.0, adjustable under Advanced) between UNETLoader and QwenImage21Cache. The LoRA repo and file are settings
  (default `f23gg/NSFW-LORA-Qwen-Image-2.1` / `NSFW Qwen Lora.safetensors`, 159 MB, downloaded with the other models).
  A badge "NSFW on" shows next to Generate.
- It is **never applied to photo edits** (Edit / Remove background): edited photos usually show real people.
- The LoRA may not fit the int8 model files. The kernel applies it once during GPU 2's warm-up and checks ComfyUI's
  log for "lora key not loaded". If it fails, `/health` says `nsfw: "unavailable"`, the app shows
  "NSFW detail unavailable on this server", and jobs run without it instead of failing.
- **Minors safety block** (always on, enforced in the Kaggle gateway so every client is covered, and also in the app):
  a prompt, edit instruction or improved prompt is refused (HTTP 422, `blocked: true`) when it combines a minor term
  (child, kid, minor, teen, underage, schoolgirl, loli, young-looking, ages under 18 in digits or words, "15 year old",
  "aged 14", Nepali/Hindi words such as keti, baccha, nani, chhori, balika…) with sexual or nude content. With NSFW on,
  any minor term is refused. Message: *"Blocked: this request combines minors with sexual or nude content…"*.
  Tests: `tests/test_kernel.py`.

## Gateway API (for the Windows app and future phone apps)
Base URL = the tunnel address (announced on `https://ntfy.sh/<topic>` as JSON messages `{"type": "url", "url": ...}`,
then `ready`, `error`, `stopped`; poll with `https://ntfy.sh/<topic>/json?poll=1&since=<unix time>`).
Every request needs the header `X-Key: <key>` (or `?key=`). Topic and key are **persistent** (kept in
`data/state.json`); the laptop shows them at `GET http://localhost:8766/api/pairing` (laptop only) for pairing a phone.

| Method & path | Body / query | Answer |
|---|---|---|
| `GET /health` (= `/api/status`) | | `stage, pct, ready, error, gpus[{prompt_id,value,max}], pe{t2i,i2i}, nsfw, nsfw_detail, wf_check, uptime, idle_s, idle_limit_s, max_s, sizes, queue` |
| `POST /api/upload` | multipart field `file` (PNG/JPG/WEBP, ≤ 40 MB) | `{name}` to use as `image` |
| `POST /api/jobs` | `{kind:"create", prompt, negative?, aspect:"1:1"\|"3:4"\|"4:3"\|"9:16"\|"16:9", quality:"turbo"\|"quality", count:1-4, seed:-1, nsfw?, lora_strength?, source?}` | `{jobs:[job]}` |
| | `{kind:"edit", prompt, image, count:1-4, seed}` / `{kind:"rembg", image}` | |
| | `{kind:"improve", prompt, mode:"t2i"\|"i2i", image? (i2i), aspect?}` | job; its `text` is the improved prompt |
| `GET /api/jobs?since=<unix>` | | `{jobs:[...], status:{...}, now}` (all active jobs + those changed since) |
| `GET /api/jobs/{id}` | | job |
| `GET /api/jobs/{id}/image` | | the PNG |
| `POST /api/jobs/{id}/cancel` | | job |
| `DELETE /api/jobs/{id}` (or `POST /api/jobs/{id}/delete`) | | `{ok, deleted}`: removes the job and its image from the server. A queued job is cancelled and removed; a running one is interrupted and removed when it stops (`pending: true`). Unknown ids answer `deleted: false`. Used by "Delete" in My images, "Clear finished" and the phone's "Hide". |
| `POST /shutdown` | | ends the Kaggle session |
| `* /g0/...`, `/g1/...` | | raw ComfyUI of GPU 1 / 2 (debugging, `/g0/object_info`) |

Job fields: `id, kind, status (queued|submitting|running|done|failed|cancelled), gpu, progress 0-1, phase, created,
started, finished, took, error, note, blocked, source, text (improve), params, result {w,h,bytes}, estimate, eta`.
Errors: 400 bad request, 401 bad key, 422 refused by the safety block. Only `POST` and `DELETE` requests (submit, upload,
cancel, delete) and running jobs count as activity for the idle timer; polling and image downloads do not keep the GPUs on.

**Several apps, one server.** The Windows app and the phone app share the key, the ntfy topic and the kernel slug.
Pressing On first looks for a server on the topic and joins it if `/health` answers; if Kaggle says the kernel is
already running/queued but it has not announced an address yet, it waits for it. It never deletes a running session.
While the Windows app is off it checks the topic every 60 s and joins a server started from the phone by itself
(sends its waiting jobs, downloads the phone's images, runs auto-off; phone activity counts via the gateway's `idle_s`).
After the laptop turns a server off it ignores that session's old messages.

## Kaggle account (one place)
`kaggle_ctl.py` holds everything account-specific; `data/settings.json` can override it:
`kaggle_user` (leomaharjan), `kaggle_slug` (qwen-image-studio-server), `kaggle_token_file` (`~/.kaggle-leo6/access_token`).
`KAGGLE_CONFIG_DIR` points at that token's folder, never `~/.kaggle`. A client app with per-user Kaggle sign-in only
needs these three values plus its own topic and key.

## Files
- `app_main.py`: the Windows app (pywebview window + server). Built into `app\QwenImageStudio\QwenImageStudio.exe`.
- `server.py`: local server and UI API (port 8766), outbox, library, ntfy discovery, PIN for phones.
- `kaggle_ctl.py`: push / status / delete / quota (account settings above). `kaggle/qis_server.py`: the kernel template.
- `web/`: UI (`index.html`, `app.js`, `style.css` = Wan Studio's style + image parts). `prompts/`: prompt-enhancer system prompts.
- `templates/`: official Comfy-Org workflows. `kaggle/smoke_test.py`, `smoke_out/`: the first Kaggle test.
- `data/`: settings (PIN), state (topic, key), outbox, library, logs (`server.log`, `app.log`), uploads, thumbs.
- `tests/test_kernel.py`: safety + workflow unit tests. `tests/test_e2e.py`: full local test (see below).
  `tests/test_phone_flows.py SCRATCH_DIR`: joining a phone-started server, attach-while-off, delete, idle rules (21 checks).
  `tests/live_run.py`, `tests/live_diag_rembg.py`: the live Kaggle test driver and the background-removal diagnostic.
- `tools/fake_comfy.py`, `tools/fake_kaggle.py`: local stand-ins (no GPU, no Kaggle). `tools/shots.py`: UI screenshots. `tools/make_icon.py`.
- `build_app.sh`: rebuild the exe after changing `server.py`, `kaggle_ctl.py` or `app_main.py` (uses Wan Studio's
  `.venv-app` Python). `web/`, `kaggle/`, `prompts/` changes need only an app restart. `Qwen Image Studio.vbs`: fallback launcher.

## Testing without Kaggle
```
PY="../Wan-Studio/.venv/Scripts/python.exe"
$PY -B tests/test_kernel.py                                   # unit tests
QIS_HOME=<copy of the project> QIS_FAKE_KAGGLE=1 $PY -B server.py &   # kernel runs locally with fake ComfyUIs
$PY -B tests/test_e2e.py                                      # 39 checks: outbox, power, queue, both GPUs, PE, safety, NSFW, cancel, power off
```
Use a copy of the project with its own `data/settings.json` (`output_dir`) so test images don't land in Pictures.

## Speed and GPU hours (live test 2026-11-05, 2x T4, through the app)
| Step | Time | Result |
|---|---|---|
| Power on -> ready | 4 min 52 s (kernel: setup 154 s, warm-up 76 s per GPU) | self-test: all 7 workflows OK; NSFW LoRA "ok (0 keys not loaded)" |
| Turbo create 1:1 | 70 s (first real image) | clean |
| Quality create 3:4 | 179 s | clean (small sign text misspelled) |
| Edit "make it night with snow falling" | 78 s | clean, subject kept |
| Remove background | 299 s | **broken** (see Known issues) |
| Improve my prompt (Create) | 390 s | good, detailed prompt |
| 2 Turbo 16:9 at once | 62 s (GPU 2) + 83 s (GPU 1, had to unload the prompt helper) | both clean, ran in parallel |

The whole session used about 25 GPU minutes (0.41 h of the 30 h weekly quota).

## Known issues
- **Remove background** gave a broken picture in the live test (alpha almost 0, black 16-px blocks), with both the
  Quality and the Turbo model. Cause: on a T4 (no bf16) ComfyUI ran the VAE in fp16 (`VAE ... dtype: torch.float16`)
  and the RGBA decode overflowed. Fixed in the kernel by starting ComfyUI with `--fp32-vae`; **not yet verified live**.
- The prompt helper takes about 6 minutes on a T4 (reasoning + rewrite, up to 4096 tokens) and unloads the image model
  on that GPU (the next image there is ~20 s slower).
- Small text in images (signs) is sometimes misspelled.

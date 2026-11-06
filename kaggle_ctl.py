"""Kaggle control for Qwen Image Studio: push (start), delete (hard stop), status and GPU quota.

EVERYTHING that depends on the Kaggle account lives here (and can be overridden in data/settings.json):
  kaggle_user        Kaggle username                (default leomaharjan)
  kaggle_slug        kernel slug                    (default qwen-image-studio-server)
  kaggle_token_file  file holding the API token     (default ~/.kaggle-leo6/access_token)
A future "client" app signs each user in with their own account: it only needs these three values.
"""
import base64, json, os, secrets, shutil, tempfile

HERE = os.environ.get("QIS_HOME") or os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {"kaggle_user": "leomaharjan", "kaggle_slug": "qwen-image-studio-server",
            "kaggle_token_file": os.path.join(os.path.expanduser("~"), ".kaggle-leo6", "access_token")}


def account():
    try:
        s = json.load(open(os.path.join(HERE, "data", "settings.json"), encoding="utf-8"))
    except Exception:
        s = {}
    return {k: (s.get(k) or v) for k, v in DEFAULTS.items()}


ACC = account()
USER, SLUG = ACC["kaggle_user"], ACC["kaggle_slug"]
KERNEL = f"{USER}/{SLUG}"
os.environ.setdefault("KAGGLE_API_TOKEN", open(os.path.expanduser(ACC["kaggle_token_file"]), encoding="utf-8").read().strip())
# Own config dir (never touches ~/.kaggle/kaggle.json, which belongs to another account)
os.environ.setdefault("KAGGLE_CONFIG_DIR", os.path.dirname(os.path.expanduser(ACC["kaggle_token_file"])))

from kaggle.api.kaggle_api_extended import KaggleApi  # noqa: E402

_api = None


def api():
    global _api
    if _api is None:
        _api = KaggleApi()
        _api.authenticate()
    return _api


def kernel_source(key, topic, idle_min, max_hours, nsfw_repo="", nsfw_file=""):
    """The kernel script with its placeholders filled in (nothing is sent anywhere)."""
    src = open(os.path.join(HERE, "kaggle", "qis_server.py"), encoding="utf-8").read()
    pe = {m: open(os.path.join(HERE, "prompts", f"pe_{m}_system.txt"), encoding="utf-8").read() for m in ("t2i", "i2i")}
    rep = {"__KEY__": key, "__TOPIC__": topic, "__IDLE_MIN__": str(int(idle_min)), "__MAX_HOURS__": str(float(max_hours)),
           "__PE_SYSTEM_B64__": base64.b64encode(json.dumps(pe).encode("utf-8")).decode(),
           "__NSFW_REPO__": nsfw_repo or "", "__NSFW_FILE__": nsfw_file or ""}
    for a, b in rep.items():
        if '"' in b or "\\" in b or "\n" in b:
            raise ValueError(f"bad value for {a}")
        src = src.replace(f'"{a}"', f'"{b}"')
    return src


def push(key, topic, idle_min, max_hours, nsfw_repo="", nsfw_file=""):
    """Starts a GPU session on Kaggle. Only the app's power button calls this."""
    d = tempfile.mkdtemp()
    try:
        open(os.path.join(d, "qis_server.py"), "w", encoding="utf-8").write(kernel_source(key, topic, idle_min, max_hours, nsfw_repo, nsfw_file))
        json.dump({"id": KERNEL, "title": SLUG.replace("-", " "), "code_file": "qis_server.py", "language": "python",
                   "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_internet": True,
                   "machine_shape": "NvidiaTeslaT4", "dataset_sources": [], "competition_sources": [], "kernel_sources": []},
                  open(os.path.join(d, "kernel-metadata.json"), "w"))
        r = api().kernels_push(d)
        if getattr(r, "error", None):
            raise RuntimeError(r.error)
        return {"version": r.version_number, "url": r.url}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def status():
    """'running', 'queued', 'complete', 'error', 'cancel...', or 'none' if the kernel doesn't exist (Kaggle answers 403)."""
    try:
        r = api().kernels_status(KERNEL)
        return str(getattr(r, "status", r)).lower().split(".")[-1]
    except Exception as e:
        if any(s in str(e).lower() for s in ("404", "not found", "denied", "403")):
            return "none"
        raise


def delete():
    try:
        api().kernels_delete(KERNEL, no_confirm=True)
        return True
    except Exception as e:
        if any(s in str(e).lower() for s in ("404", "not found", "denied", "403")):
            return False
        raise


def quota():
    from kagglesdk.kernels.types.kernels_api_service import ApiGetAcceleratorQuotaStatisticsRequest
    with api().build_kaggle_client() as k:
        r = k.kernels.kernels_api_client.get_accelerator_quota_statistics(ApiGetAcceleratorQuotaStatisticsRequest())
    q = r.gpu_quota
    h = lambda td: round(td.total_seconds() / 3600, 2) if td else 0.0
    return {"used_h": h(q.time_used), "reserved_h": h(q.time_reserved), "total_h": h(q.total_time_allowed),
            "refresh": r.quota_refresh_time.isoformat() if r.quota_refresh_time else None}


def output(dest, slug=None):
    """Download a finished kernel's output files (used for the smoke test)."""
    os.makedirs(dest, exist_ok=True)
    return api().kernels_output(f"{USER}/{slug or SLUG}", path=dest, force=True, quiet=True)


def new_key():
    return secrets.token_urlsafe(24)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(KERNEL, status())
    elif cmd == "quota":
        print(quota())
    elif cmd == "delete":
        print(delete())
    elif cmd == "output":
        print(output(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    else:
        print("commands: status | quota | delete | output DEST [SLUG]   (push only from the app's power button)")

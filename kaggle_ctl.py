"""Kaggle control for Qwen Image Studio: sign-in, push (start), delete (hard stop), status and GPU quota.

EVERYTHING that depends on the Kaggle account lives here. Where the account comes from, in order:
  1. The app's own sign-in page: the token is saved in data/kaggle_token, the username in data/settings.json
     (kaggle_user). This is how the public app works: every user brings their own Kaggle account.
  2. data/settings.json kaggle_token_file (+ kaggle_user): a token file kept somewhere else.
  3. The original personal setup: ~/.kaggle-leo6/access_token (user leomaharjan), only when that file exists.
Nothing is read at import time, so the app starts fine when nobody is signed in yet.
kaggle_slug (default qwen-image-studio-server) can be changed in data/settings.json.
~/.kaggle/kaggle.json is never read or written: KAGGLE_CONFIG_DIR always points somewhere else.
"""
import base64, json, os, re, secrets, shutil, tempfile, urllib.error, urllib.request

HERE = os.environ.get("QIS_HOME") or os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("QIS_DATA") or os.path.join(HERE, "data")
TOKEN_FILE = os.path.join(DATA, "kaggle_token")          # written by the sign-in page, never shipped
DEFAULT_SLUG = "qwen-image-studio-server"
OWNER_TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".kaggle-leo6", "access_token")
OWNER_USER = "leomaharjan"
API = "https://api.kaggle.com/v1"

USER = SLUG = KERNEL = None
SOURCE = None            # "app" (signed in here), "file" (token file), or None (signed out)
_api = None
_env_set = []


class NotSignedIn(RuntimeError):
    pass


def _settings():
    try:
        return json.load(open(os.path.join(DATA, "settings.json"), encoding="utf-8"))
    except Exception:
        return {}


def _read(path):
    try:
        return open(os.path.expanduser(path), encoding="utf-8").read().strip() or None
    except OSError:
        return None


def parse_token(text, user=""):
    """Accepts a new API token (KGAT_...), a legacy key (32 hex) or a pasted kaggle.json. Returns (user, token)."""
    text = (text or "").strip()
    if text.startswith("{"):
        try:
            j = json.loads(text)
            return (j.get("username") or user or "").strip(), (j.get("key") or "").strip()
        except ValueError:
            pass
    return (user or "").strip(), text


def is_legacy(token):
    return bool(re.fullmatch(r"[0-9a-f]{32}", token or ""))


def _set_env(k, v):
    os.environ[k] = v
    if k not in _env_set:
        _env_set.append(k)


def configure():
    """(Re)reads the account. Returns True when signed in."""
    global USER, SLUG, KERNEL, SOURCE, _api
    _api = None
    for k in _env_set:                       # forget what an earlier account put into the environment
        os.environ.pop(k, None)
    _env_set.clear()
    s = _settings()
    SLUG = s.get("kaggle_slug") or DEFAULT_SLUG
    USER, SOURCE = None, None
    token = _read(TOKEN_FILE)
    if token and s.get("kaggle_user"):
        USER, SOURCE = s["kaggle_user"], "app"
        cfg = os.path.join(DATA, "kaggle-config")             # empty folder: the kaggle library looks there, not in ~/.kaggle
        os.makedirs(cfg, exist_ok=True)
        _set_env("KAGGLE_CONFIG_DIR", cfg)
        if is_legacy(token):
            _set_env("KAGGLE_USERNAME", USER)
            _set_env("KAGGLE_KEY", token)
        else:
            _set_env("KAGGLE_API_TOKEN", token)
    else:
        tf = s.get("kaggle_token_file") or (OWNER_TOKEN_FILE if os.path.exists(OWNER_TOKEN_FILE) else None)
        token = _read(tf) if tf else None
        if token:
            USER, SOURCE = s.get("kaggle_user") or OWNER_USER, "file"
            # as before: an existing KAGGLE_API_TOKEN wins; own config dir, never ~/.kaggle
            os.environ.setdefault("KAGGLE_API_TOKEN", token)
            os.environ.setdefault("KAGGLE_CONFIG_DIR", os.path.dirname(os.path.expanduser(tf)))
    KERNEL = f"{USER}/{SLUG}" if USER else None
    return SOURCE is not None


def signed_in():
    return SOURCE is not None


configure()


def api():
    global _api
    if not signed_in():
        raise NotSignedIn("Sign in to Kaggle first (Settings).")
    if _api is None:
        from kaggle.api.kaggle_api_extended import KaggleApi
        a = KaggleApi()
        try:
            a.authenticate()
        except SystemExit:                   # the kaggle library calls exit(1) when no credentials work
            raise NotSignedIn("Kaggle did not accept the saved token. Sign in again in Settings.")
        _api = a
    return _api


# ------------------------------------------------------------------ sign-in (plain REST calls, the same ones kagglesdk makes)
def _post(service_method, body, headers=None, timeout=30):
    req = urllib.request.Request(f"{API}/{service_method}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "QwenImageStudio/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def check_token(user, token):
    """Asks Kaggle whether the token works (read-only). Returns the real username or raises ValueError."""
    user, token = parse_token(token, user)
    if not token:
        raise ValueError("Paste your Kaggle API token.")
    if is_legacy(token):
        if not user:
            raise ValueError("Type your Kaggle username too (this kind of key needs it).")
        auth = base64.b64encode(f"{user}:{token}".encode()).decode()
        code, _ = _post("kernels.KernelsApiService/GetAcceleratorQuotaStatistics", {}, {"Authorization": "Basic " + auth})
        if code != 200:
            raise ValueError("Kaggle did not accept that username and key.")
        return user, token
    code, r = _post("security.OAuthService/IntrospectToken", {"token": token})
    if code != 200 or not r.get("active") or not r.get("username"):
        raise ValueError("Kaggle did not accept that token. Make a new one at kaggle.com/settings (API section).")
    return r["username"], token


def save_sign_in(user, token):
    os.makedirs(DATA, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    return configure()


def sign_out():
    try:
        os.remove(TOKEN_FILE)
    except FileNotFoundError:
        pass
    return configure()


# ------------------------------------------------------------------ the kernel
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
    if not signed_in():
        return "none"
    try:
        r = api().kernels_status(KERNEL)
        return str(getattr(r, "status", r)).lower().split(".")[-1]
    except Exception as e:
        if any(s in str(e).lower() for s in ("404", "not found", "denied", "403")):
            return "none"
        raise


def delete():
    if not signed_in():
        return False
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
        print(KERNEL, SOURCE, status())
    elif cmd == "quota":
        print(quota())
    elif cmd == "delete":
        print(delete())
    elif cmd == "output":
        print(output(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    else:
        print("commands: status | quota | delete | output DEST [SLUG]   (push only from the app's power button)")

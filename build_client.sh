#!/usr/bin/env bash
# Builds the PUBLIC Windows app: one portable folder, zipped as build/QwenImageStudio-Windows.zip.
# Every user signs in with their own Kaggle account; nothing from data/ (token, settings, keys, images) is included.
# The personal app (build_app.sh -> app/QwenImageStudio) is not touched, even while it is running.
# Uses Wan Studio's build Python (.venv-app: Python 3.12 + PyInstaller + pywebview) by absolute path; it is only read.
set -e
cd "$(dirname "$0")"
W=$(cygpath -w "$(pwd)")
PY="$(cd ../Wan-Studio/.venv-app/Scripts && pwd)/python.exe"
OUT=build/dist_client
rm -rf "$OUT" build/pyi_client
PYTHONDONTWRITEBYTECODE=1 "$PY" -m PyInstaller --noconfirm --clean --windowed --name QwenImageStudio --icon "$W\web\qis.ico" \
  --distpath "$OUT" --workpath build/pyi_client --specpath build/client \
  --collect-all webview --collect-all kagglesdk --collect-all kaggle --hidden-import server --hidden-import kaggle_ctl --paths "$W" app_main.py > build/build_client_log.txt 2>&1
tail -1 build/build_client_log.txt
APP="$OUT/QwenImageStudio"
mkdir -p "$APP/kaggle" "$APP/prompts"
cp -r web "$APP/web"
cp kaggle/qis_server.py "$APP/kaggle/"
cp prompts/pe_t2i_system.txt prompts/pe_i2i_system.txt "$APP/prompts/"
rm -rf "$APP/data"
"$PY" - "$OUT" <<'EOF'
import os, sys, zipfile
out = sys.argv[1]
src = os.path.join(out, "QwenImageStudio")
dst = os.path.join(os.path.dirname(out), "QwenImageStudio-Windows.zip")
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("data", "__pycache__")]
        for f in files:
            p = os.path.join(root, f)
            z.write(p, os.path.join("Qwen Image Studio", os.path.relpath(p, src)))
print("zip:", dst, round(os.path.getsize(dst) / 1e6, 1), "MB")
EOF

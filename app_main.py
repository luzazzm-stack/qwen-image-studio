"""Qwen Image Studio for Windows: the app window (WebView2) plus the local server in one program.

Personal build (build_app.sh): app/QwenImageStudio/QwenImageStudio.exe; data and the UI files stay in the project
folder, so the app can be updated without rebuilding.
Public build (build_client.sh): one portable folder with web/, kaggle/ and prompts/ next to the exe; its data (sign-in,
settings, queue) lives in %LOCALAPPDATA%/Qwen Image Studio, never next to the exe.
"""
import ctypes, json, os, sys, threading, time, urllib.request

if getattr(sys, "frozen", False):
    EXE_DIR = os.path.dirname(sys.executable)
    if os.path.isdir(os.path.join(EXE_DIR, "web")):          # portable public build
        HOME = EXE_DIR
        os.environ.setdefault("QIS_DATA", os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Qwen Image Studio", "data"))
    else:
        HOME = os.path.abspath(os.path.join(EXE_DIR, "..", ".."))
else:
    HOME = os.path.dirname(os.path.abspath(__file__))
os.environ["QIS_HOME"] = HOME
DATA = os.environ.get("QIS_DATA") or os.path.join(HOME, "data")
os.makedirs(DATA, exist_ok=True)
sys.stdout = sys.stderr = open(os.path.join(DATA, "app.log"), "a", buffering=1, encoding="utf-8")
sys.path.insert(0, HOME)

URL = "http://127.0.0.1:8766"


def server_up():
    try:
        urllib.request.urlopen(URL + "/api/state", timeout=1.5).read()
        return True
    except Exception:
        return False


def main():
    own_server = False
    loop_box = {}
    if server_up():
        # Another copy is already running: if it has a window, bring it to the front and leave.
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(URL + "/api/show", method="POST"), timeout=3).read())
            if r.get("shown"):
                return
        except Exception:
            pass
    else:
        import asyncio
        import server
        from aiohttp import web

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_box["loop"] = loop
            web.run_app(server.make_app(), host="0.0.0.0", port=server.PORT, print=None, access_log=None, handle_signals=False, loop=loop)

        threading.Thread(target=run, daemon=True).start()
        own_server = True
        for _ in range(60):
            if server_up():
                break
            time.sleep(0.25)

    import webview
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TheEverestHoliday.QwenImageStudio")
    except Exception:
        pass
    webview.settings["ALLOW_DOWNLOADS"] = True
    win = webview.create_window("Qwen Image Studio", URL, width=1380, height=900, min_size=(420, 640), background_color="#000000")

    if own_server:
        import server

        def show():
            win.show()
            win.restore()
        server.show_window = show

        def on_closing():
            busy = server.state.get("power") in ("starting", "ready") or any(
                j["status"] in server.ACTIVE for j in server.remote)
            if busy and win.create_confirmation_dialog(
                    "Qwen Image Studio", "The Kaggle server is on or images are still being made.\n\n"
                    "OK: keep Qwen Image Studio working in the background (open it again from the Start menu).\n"
                    "Cancel: quit and turn the Kaggle server off."):
                win.hide()
                return False
            return True
        win.events.closing += on_closing

    webview.start()

    if own_server and loop_box.get("loop"):
        import asyncio
        import server
        if server.state.get("power") in ("starting", "ready"):
            try:
                asyncio.run_coroutine_threadsafe(server.power_off("Qwen Image Studio was closed"), loop_box["loop"]).result(timeout=40)
            except Exception as e:
                print("power off on exit failed", e)


if __name__ == "__main__":
    main()

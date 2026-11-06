"""UI screenshots like the user's screen (1280x720 CSS px at 150% scaling) plus a phone layout.
Needs a running server (port 8766). Run: uv run --no-project --with playwright python tools/shots.py OUT_DIR [PHOTO]
Uses the installed Chrome (channel="chrome"), headless."""
import asyncio, json, os, sys, urllib.request
from playwright.async_api import async_playwright

OUT = sys.argv[1]
PHOTO = sys.argv[2] if len(sys.argv) > 2 else None
URL = "http://localhost:8766"
os.makedirs(OUT, exist_ok=True)


def api(path, body=None):
    r = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    return json.loads(urllib.request.urlopen(r, timeout=30).read() or b"null")


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        ctx = await b.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1.5)
        pg = await ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)

        async def shot(name, hash_, full=False, wait=1800):
            await pg.goto(f"{URL}/#{hash_}")
            await pg.reload()          # a hash-only goto keeps the old page (and any open dialog)
            await pg.wait_for_timeout(wait)
            await pg.screenshot(path=os.path.join(OUT, name), full_page=full)

        # server off
        await shot("01_create_off.png", "create")
        await shot("02_create_off_full.png", "create", full=True)
        # edit with a photo
        await pg.goto(f"{URL}/#edit")
        await pg.wait_for_timeout(1200)
        if PHOTO:
            await pg.set_input_files("#eFile", PHOTO)
            await pg.fill("#ePrompt", "Make it a snowy evening, keep the teahouse and the trekker exactly the same")
            await pg.dispatch_event("#ePrompt", "input")
            await pg.wait_for_timeout(1500)
        await pg.screenshot(path=os.path.join(OUT, "03_edit.png"))
        await shot("04_gallery.png", "gallery")
        await pg.click(".icard .thumb")
        await pg.wait_for_timeout(1200)
        await pg.screenshot(path=os.path.join(OUT, "05_viewer.png"))
        await shot("06_queue_off.png", "queue")
        await shot("07_settings.png", "settings", full=True)

        # server on (fake kernel, slow fake GPUs) with work in progress, NSFW on
        api("/api/settings", {"nsfw": True})
        api("/api/power", {"on": True})
        for _ in range(60):
            if api("/api/state")["power"] == "ready":
                break
            await asyncio.sleep(1)
        api("/api/submit", {"kind": "create", "prompt": "A snow leopard on a rocky ridge at sunrise, telephoto wildlife photo", "aspect": "16:9", "count": 3})
        await asyncio.sleep(6)
        await shot("08_create_working.png", "create", wait=2500)
        await shot("09_queue_working.png", "queue", wait=2500)
        await shot("10_settings_on.png", "settings", full=True)

        # phone
        ph = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        pp = await ph.new_page()
        pp.on("pageerror", lambda e: errors.append("phone: " + str(e)))
        for name, h in (("11_phone_create.png", "create"), ("12_phone_edit.png", "edit"), ("13_phone_gallery.png", "gallery"),
                        ("14_phone_queue.png", "queue"), ("15_phone_settings.png", "settings")):
            await pp.goto(f"{URL}/#{h}")
            await pp.reload()
            await pp.wait_for_timeout(1800)
            await pp.screenshot(path=os.path.join(OUT, name), full_page=True)
        api("/api/settings", {"nsfw": False})
        api("/api/power", {"on": False})
        await b.close()
        print("errors:", errors or "none")

asyncio.run(main())

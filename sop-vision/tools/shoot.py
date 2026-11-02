"""视觉闭环截图脚本：按规范断点对各页面截图（开发用）。
用法：python tools/shoot.py http://127.0.0.1:8765 out_dir [dark|light] [页面名]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SIZES = [(1920, 1080), (1280, 800), (980, 800), (480, 900)]
PAGES = [("overview", "#/overview"), ("live", "#/live/DEMO-ST01"), ("trace", "#/trace"), ("config", "#/config/DEMO-ST01"), ("inspection", "#/inspection")]


def main(base: str, out: str, theme: str = "dark", only: str | None = None) -> None:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w, h in SIZES:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base)
            page.evaluate(f"localStorage.setItem('visual.theme','{theme}')")
            for name, hash_ in PAGES:
                if only and name != only:
                    continue
                page.goto(f"{base}/{hash_}")
                page.reload()
                page.wait_for_timeout(1800)
                page.screenshot(path=str(out_dir / f"{name}_{w}_{theme}.png"))
            if errors:
                print(f"[{w}] console errors:", *sorted(set(errors)), sep="\n  ")
            ctx.close()
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *(sys.argv[3:5]))

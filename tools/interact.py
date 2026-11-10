"""交互验收脚本（开发用）：抽屉 / 大图 / Esc / 主题切换 / 复制。输出截图并打印断言结果。
用法：python tools/interact.py http://127.0.0.1:8765 out_dir
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(base: str, out: str) -> int:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            failures.append(name)

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1920, "height": 1080}, permissions=["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        page.goto(f"{base}/#/trace")
        page.wait_for_selector("tr[data-id]")
        page.click("tr[data-id] >> nth=1")
        page.wait_for_selector(".drawer-body .gantt")
        page.wait_for_timeout(600)
        page.screenshot(path=str(out_dir / "drawer_1920.png"))
        check("抽屉打开并渲染甘特图", page.locator(".drawer-body .gantt").count() == 1)
        check("抽屉含证据缩略图", page.locator(".thumb").count() >= 2)

        page.click(".thumb >> nth=0")
        page.wait_for_selector(".lightbox img")
        page.wait_for_timeout(400)
        page.screenshot(path=str(out_dir / "lightbox_1920.png"))
        loaded = page.eval_on_selector(".lightbox img", "i => i.complete && i.naturalWidth > 0")
        check("大图加载成功", loaded)
        page.keyboard.press("Escape")
        check("Esc 先关闭大图（抽屉仍在）", page.locator(".lightbox").count() == 0 and page.locator(".drawer").count() == 1)
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        check("再按 Esc 关闭抽屉", page.locator(".drawer-wrap").count() == 0)

        page.click("[data-v=FAIL]")
        page.wait_for_timeout(700)
        results = page.eval_on_selector_all("tr[data-id] .badge span", "els => [...new Set(els.map(e => e.textContent))]")
        check("按“不合格”筛选后只剩不合格", results == ["不合格"])

        page.click("tr[data-id] >> nth=0")
        page.wait_for_selector("[data-copy]")
        page.click("[data-copy] >> nth=0")
        page.wait_for_selector(".toast")
        check("复制后出现 toast 反馈", "已复制" in page.inner_text(".toast"))
        page.keyboard.press("Escape")

        page.click("#theme-btn")
        page.wait_for_timeout(300)
        check("切换为浅色主题", page.evaluate("document.documentElement.dataset.theme") == "light")
        page.goto(f"{base}/#/overview")
        page.wait_for_timeout(1800)
        page.screenshot(path=str(out_dir / "overview_light_1920.png"))
        page.goto(f"{base}/#/config/DEMO-ST01")
        page.wait_for_timeout(1500)
        page.click("[data-tab=roi]")
        page.wait_for_timeout(400)
        page.screenshot(path=str(out_dir / "config_roi_light_1920.png"))
        disabled = page.eval_on_selector("[data-calib]", "b => b.disabled && !!b.title")
        check("仿真工位的标定按钮禁用且有原因说明", disabled)

        page.click("#accent-btn")
        page.click("[data-accent='#b08cf0']")
        accent = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--accent-primary').trim()")
        check("主题色切换生效", accent == "#b08cf0")
        page.screenshot(path=str(out_dir / "config_purple_light_1920.png"))
        check("无控制台错误", not errors)
        if errors:
            print(*sorted(set(errors)), sep="\n  ")
        b.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))

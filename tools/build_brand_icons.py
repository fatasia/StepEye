"""Export the shared SVG application icon to PNG and Windows ICO.

Run: python tools/build_brand_icons.py (requires Playwright Chromium and Pillow).
"""
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parents[1] / "web" / "assets"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 512, "height": 512}, device_scale_factor=1)
        page.goto((ASSETS / "stepeye-icon.svg").as_uri())
        page.screenshot(path=str(ASSETS / "stepeye-512.png"), omit_background=True)
        browser.close()
    with Image.open(ASSETS / "stepeye-512.png") as source:
        source.resize((180, 180), Image.Resampling.LANCZOS).save(ASSETS / "stepeye-180.png")
        source.save(ASSETS / "stepeye.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])

if __name__ == "__main__":
    main()

"""分类导航和应用入口的浏览器交互。"""
import socket
import threading

import pytest

from visual.server import build_app


def test_navigation_groups_and_mobile_drawer(tmp_path):
    pwapi = pytest.importorskip('playwright.sync_api')
    import uvicorn

    app = build_app([], tmp_path / 'data')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        with pwapi.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{port}/#/applications', wait_until='domcontentloaded')
            page.locator('[data-nav="applications"][aria-current="page"]').wait_for()
            assert page.locator('#nav > [data-nav="applications"]').count() == 1
            routes = page.locator('#nav [data-nav]').evaluate_all('(items) => items.map((e) => e.dataset.nav)')
            assert len(routes) == len(set(routes)) == 18
            assert page.locator('.nav-head span:not(.chev)').all_inner_texts() == ['运行监控', '视频巡防', '质量视觉', '数据知识']
            assert page.locator('[data-group="data"] .nav-items').evaluate('(e) => e.inert')
            page.locator('[data-group-toggle="data"]').click()
            page.locator('[data-nav="labeling"]').click()
            page.locator('[data-nav="labeling"][aria-current="page"]').wait_for()
            assert page.locator('[data-group-toggle="data"]').get_attribute('aria-expanded') == 'true'
            assert page.locator('[data-nav="samples"]').get_attribute('aria-current') is None
            page.reload(wait_until='domcontentloaded')
            page.locator('[data-nav="labeling"][aria-current="page"]').wait_for()
            assert page.locator('[data-group-toggle="data"]').get_attribute('aria-expanded') == 'true'
            page.set_viewport_size({'width': 1180, 'height': 800})
            assert page.locator('[data-group-toggle="quality"]').is_visible()
            assert not page.locator('#nav-toggle').is_visible()
            page.set_viewport_size({'width': 980, 'height': 800})
            page.locator('#nav-toggle').click()
            assert page.locator('#app').evaluate('(e) => e.classList.contains("nav-open")')
            page.locator('[data-nav="applications"]').click()
            page.locator('[data-nav="applications"][aria-current="page"]').wait_for()
            assert not page.locator('#app').evaluate('(e) => e.classList.contains("nav-open")')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#nav-toggle').click()
            assert page.locator('[data-group-toggle="quality"]').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.evaluate("localStorage.setItem('visual.nav-groups', JSON.stringify(['ops','video','quality','data','tools']))")
            page.reload(wait_until='domcontentloaded')
            page.locator('[data-nav="applications"][aria-current="page"]').wait_for()
            assert page.locator('.nav-head[aria-expanded="true"]').count() == 4
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()

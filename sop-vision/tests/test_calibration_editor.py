"""Real browser/API geometry check, not physical camera precision."""
import socket,threading
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from visual.calibration import CalibrationStore
from visual.calibration_routes import router


def test_calibration_editor_real_browser(tmp_path):
    pwapi=pytest.importorskip('playwright.sync_api')
    import uvicorn
    root=Path(__file__).resolve().parents[1];store=CalibrationStore(tmp_path/'cal.sqlite')
    app=FastAPI();app.include_router(router(store))
    @app.get('/')
    def page_html():
        css=''.join(f'<link rel="stylesheet" href="/css/{s}.css">' for s in ['tokens','base','shell','components','pages'])
        return HTMLResponse('<html lang="zh-CN"><meta charset="utf-8">'+css+'<body><div id="layer"></div><script type="module">import {openCalibrationEditor} from "/js/pages/calibration-editor.js";openCalibrationEditor({canWrite:true,scopeId:"editor-test"});</script></body></html>')
    app.mount('/js',StaticFiles(directory=root/'web/js'));app.mount('/css',StaticFiles(directory=root/'web/css'))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    photo=tmp_path/'plane.png';Image.new('RGB',(640,480),'#253644').save(photo)
    artifacts=root.parent/'.v0/output/playwright/calibration-editor';artifacts.mkdir(parents=True,exist_ok=True)
    try:
        with pwapi.sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1920,'height':1080});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(f'http://127.0.0.1:{port}/');page.locator('[data-ce-file]').set_input_files(str(photo));canvas=page.locator('[data-ce-canvas]');canvas.wait_for(state='visible')
                def pick(x,y):
                    canvas.scroll_into_view_if_needed();b=canvas.bounding_box();page.mouse.click(b['x']+1+x*(b['width']-2)/640,b['y']+1+y*(b['height']-2)/480)
                def fill(values):
                    for i,(x,y) in enumerate(values):
                        page.locator(f'[data-row="{i}"][data-axis="0"]').fill(str(x));page.locator(f'[data-row="{i}"][data-axis="1"]').fill(str(y))
                def add(values):
                    for x,y in values:pick(x,y)
                    actual=[tuple(map(int,t.inner_text().split(","))) for t in page.locator("[data-ce-rows] small").all()]
                    assert all(abs(a-x)<=1 and abs(b-y)<=1 for (a,b),(x,y) in zip(actual,values))
                    fill(actual)
                add([(80,80),(560,80),(560,400),(80,400)])
                pick(80,80);assert '重复' in page.locator('[data-ce-error]').inner_text()
                page.locator('[data-ce-mode="checks"]').click();add([(200,200),(400,300)])
                page.locator('[data-ce-save]').click();page.wait_for_function('document.querySelector("[data-ce-result]").textContent.includes("验证通过")')
                assert store.list()[0]['validated'] and store.list()[0]['validation_max']<.01;page.screenshot(path=str(artifacts/'validated_1920.png'))
                page.locator('[data-row="0"][data-axis="0"]').fill('900');page.locator('[data-ce-save]').click()
                page.wait_for_function('document.querySelector("[data-ce-result]").textContent.includes("验证未通过")')
                assert not store.list()[0]['validated'];assert '不可用于测量' in page.locator('[data-ce-error]').inner_text()
                page.locator('[data-ce-clear]').click();page.locator('[data-ce-mode="points"]').click();add([(100,100),(200,100),(300,100),(400,100)])
                page.locator('[data-ce-mode="checks"]').click();add([(150,100),(250,100)]);page.locator('[data-ce-save]').click()
                page.wait_for_function('document.querySelector("[data-ce-error]").textContent.includes("共线")')
                assert len(store.list())==2;page.screenshot(path=str(artifacts/'collinear_1920.png'))
                # LAN HTTP has no Web Crypto: image picking still works with an explicit reference.
                page.evaluate("Object.defineProperty(window.crypto, 'subtle', {value:undefined, configurable:true})")
                page.locator('[data-ce-file]').set_input_files([])
                page.locator('[data-ce-file]').set_input_files(str(photo));canvas.wait_for(state='visible')
                page.wait_for_function('document.querySelector("[data-ce-error]").textContent.includes("HTTP")')
                assert page.locator('[data-ce-rows] tr').count()==0
                assert page.locator('[data-ce-reference]').input_value()==''
                assert page.locator('.cal-editor details').evaluate('(e)=>e.open')
                add([(80,80),(560,80),(560,400),(80,400)])
                page.locator('[data-ce-mode="checks"]').click();add([(200,200),(400,300)])
                page.locator('[data-ce-save]').click()
                assert '图像参考' in page.locator('[data-ce-error]').inner_text()
                page.locator('[data-ce-reference]').fill('plane-camera-A-v1');page.locator('[data-ce-save]').click()
                page.wait_for_function('document.querySelector("[data-ce-result]").textContent.includes("验证通过")')
                assert len(store.list())==3 and store.list()[0]['validated']
                assert store.list()[0]['reference']=='plane-camera-A-v1'
                assert not errors
            finally:browser.close()
    finally:server.should_exit=True;thread.join(10);store.close();sock.close()

"""曝光建库及相机向导的浏览器流程；设备调用使用受控替身。"""
import socket
import threading

import cv2
import numpy as np
import pytest

from visual.server import build_app


def test_exposure_and_camera_wizard(tmp_path):
    pwapi=pytest.importorskip('playwright.sync_api')
    import uvicorn
    app=build_app([],tmp_path/'data')
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    image=np.full((112,112,3),(50,80,100),np.uint8);image[20:50,20:50]=(70,160,140)
    photo=tmp_path/'ref.png';assert cv2.imwrite(str(photo),image)
    try:
        with pwapi.sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':900});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            models=[{'id':'warehouse','name':'general','available':True,'task':'detect','adapter':'yolo_raw','classes':['fire','smoke','person']},
                    {'id':'public-fire','name':'fire','available':True,'task':'detect','adapter':'yolo_raw','classes':['fire','smoke']}]
            page.route('**/api/models',lambda route:route.fulfill(json=models))
            page.goto(f'http://127.0.0.1:{port}/#/applications',wait_until='domcontentloaded')
            page.locator('[data-new-app]').wait_for(state='visible');page.locator('[data-new-app]').click()
            assert page.locator('[data-model]').input_value()=='public-fire'
            page.locator('[data-name]').fill('appearance');page.locator('[data-task]').select_option('appearance')
            page.locator('[data-create]').click();page.get_by_role('button',name='appearance').click()
            page.locator('[data-appearance]').click();page.locator('[data-lighting]').evaluate('(e)=>e.closest("details").open=true')
            page.locator('[data-lighting]').select_option('auto');page.locator('[data-ok]').set_input_files(str(photo))
            page.locator('[data-register]').click();page.get_by_text('已登记 1 张良品，可以检查图片。').wait_for()
            page.locator('[data-image]').set_input_files(str(photo));page.locator('[data-preview-exposure]').click()
            page.locator('[data-exposure-preview]').wait_for(state='visible')
            page.locator('[data-inspect]').click();page.wait_for_function("document.querySelector('[data-result]').textContent.includes('正常')")
            page.keyboard.press('Escape')
            page.route('**/api/cameras/onvif/discover',lambda route:route.fulfill(json={'ok':True,'devices':[{'xaddr':'http://192.168.1.10:8080/onvif/device_service','scopes':[]}]}))
            page.route('**/api/cameras/onvif/adopt',lambda route:route.fulfill(json={'ok':True,'rtsp':'rtsp://192.168.1.10/stream'}))
            sent=[]
            def input_route(route):
                sent.append(route.request.post_data_json);route.fulfill(json={'width':640,'height':480,'image':'eA=='})
            page.route('**/api/applications/example/input',input_route)
            page.evaluate("async()=>{const m=await import('/js/pages/application-form.js');m.connectInput({id:'example',name:'camera'},()=>{})}")
            page.locator('[data-type]').select_option('onvif');page.locator('[data-discover]').click()
            page.locator('[data-onvif-devices] option').nth(1).wait_for(state='attached')
            page.locator('[data-onvif-devices]').select_option(index=1);page.locator('[data-onvif-user]').fill('u');page.locator('[data-onvif-password]').fill('p')
            page.locator('[data-test]').click();page.locator('[data-trial]:not([disabled])').wait_for()
            assert sent[-1]['type']=='rtsp' and 'u:p@' in sent[-1]['uri']
            assert page.locator('[data-onvif-password]').input_value()==''
            page.locator('[data-type]').select_option('gb28181');assert page.locator('[data-gb-device]').is_visible()
            assert not errors
            browser.close()
    finally:
        server.should_exit=True;thread.join(15);sock.close()

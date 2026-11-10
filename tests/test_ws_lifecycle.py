"""An idle websocket must release its Hub queue before client context teardown."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from visual.auth import AuthStore
from visual.server import build_app


async def wait_released(hub):
    for _ in range(100):
        children = [t for t in asyncio.all_tasks()
                    if not t.done() and t.get_name().startswith('stepeye-ws-')]
        if not hub.clients and not children:
            return
        await asyncio.sleep(.005)
    raise AssertionError('WebSocket queue or child task leaked after disconnect')


def test_empty_station_idle_disconnect_releases_queue_without_outgoing_message(tmp_path):
    app = build_app([], tmp_path/'data')
    hub = app.state.manager.hub
    with TestClient(app) as client:
        with client.websocket_connect('/ws') as socket:
            assert socket.receive_json() == {'type':'hello','stations':[]}
            assert len(hub.clients) == 1
            socket.close()
            # Still inside websocket context: Starlette teardown cancellation cannot hide a leak.
            client.portal.call(wait_released, hub)
        assert not hub.clients


def test_push_delivery_and_incoming_payload_ignored_then_close(tmp_path):
    app = build_app([], tmp_path/'data')
    hub = app.state.manager.hub
    with TestClient(app) as client:
        for _ in range(3):
            with client.websocket_connect('/ws') as socket:
                socket.receive_json()
                socket.send_json({'type':'ignored','command':'must-not-execute'})
                hub.publish({'type':'test','value':42})
                assert socket.receive_json() == {'type':'test','value':42}
                socket.close()
                client.portal.call(wait_released, hub)
        assert not hub.clients


def test_unauthenticated_socket_never_registers_queue(tmp_path):
    auth = AuthStore(tmp_path/'auth.db')
    app = build_app([], tmp_path/'data', auth=auth)
    hub = app.state.manager.hub
    try:
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect) as rejected:
                with client.websocket_connect('/ws'):
                    pass
            assert rejected.value.code == 4401
            assert not hub.clients
    finally:
        auth.close()

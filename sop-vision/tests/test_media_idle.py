"""Preview leases allocate a deadline waiter only while sessions exist."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from visual.media import MediaBridge, MediaError


def bridge_factory(**kwargs):
    deleted = threading.Event()
    def handle(req):
        if req.method == 'POST':
            return httpx.Response(201, content=b'v=0', headers={'Location': '/webrtc/session/1'})
        deleted.set()
        return httpx.Response(204)
    return MediaBridge('http://media', transport=httpx.MockTransport(handle), **kwargs), deleted


def wait_retired(bridge):
    deadline = time.monotonic() + 2
    while bridge._sweeper is not None and time.monotonic() < deadline:
        time.sleep(.005)
    assert bridge._sweeper is None


def test_no_worker_without_sessions_and_restart_after_release():
    assert MediaBridge()._sweeper is None
    bridge, _ = bridge_factory()
    assert bridge._sweeper is None
    for _ in range(2):
        _, token = bridge.offer('live/test', b'v=0', 'ST', 'u')
        assert bridge._sweeper is not None
        bridge.release(token, 'ST', 'u')
        wait_retired(bridge)
    bridge.close()


def test_deadline_expires_without_periodic_server_task():
    bridge, deleted = bridge_factory(lease_s=.03)
    bridge.offer('live/test', b'v=0', 'ST', 'u')
    assert deleted.wait(2)
    wait_retired(bridge)
    assert not bridge._sessions
    bridge.close()


def test_concurrent_offers_and_releases_do_not_lose_worker():
    bridge, _ = bridge_factory()
    def cycle(_):
        _, token = bridge.offer('live/test', b'v=0', 'ST', 'u')
        bridge.release(token, 'ST', 'u')
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(cycle, range(40)))
    wait_retired(bridge)
    assert not bridge._sessions and bridge._pending == 0
    bridge.close()


def test_close_during_offer_cleans_late_session():
    entered, proceed, deleted = threading.Event(), threading.Event(), threading.Event()
    def handle(req):
        if req.method == 'POST':
            entered.set()
            assert proceed.wait(2)
            return httpx.Response(201, content=b'v=0', headers={'Location': '/webrtc/session/1'})
        deleted.set()
        return httpx.Response(204)
    bridge = MediaBridge('http://media', transport=httpx.MockTransport(handle))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bridge.offer, 'live/test', b'v=0', 'ST', 'u')
        assert entered.wait(2)
        bridge.close()
        proceed.set()
        with pytest.raises(MediaError) as error:
            future.result(2)
        assert error.value.status == 503
    assert deleted.is_set()
    assert bridge._sweeper is None and not bridge._sessions
    with pytest.raises(MediaError):
        bridge.offer('live/test', b'v=0', 'ST', 'u')

"""GB28181 wvp 适配器测试(假 HTTP,不联网)与 geo 同步。"""
from __future__ import annotations

import pytest

from visual.gb28181 import WvpClient
from visual.geo import GeoStore


class FakeHttp:
    def __init__(self):
        self.gets: list[str] = []

    def post(self, url, payload, headers=None):
        assert payload["username"] == "admin"
        import hashlib
        assert payload["password"] == hashlib.md5(b"pw").hexdigest()
        return {"code": 0, "data": {"accessToken": "T123"}}

    def get(self, url):
        self.gets.append(url)
        assert "access-token=T123" in url
        return {"data": {"list": [
            {"deviceId": "34020000001320000001", "name": "东门枪机",
             "latitude": "31.23", "longitude": "121.47", "longitudeTag": "GPS"},
            {"deviceId": "34020000001320000002", "name": "西门枪机",
             "latitude": "0", "longitude": "0", "longitudeTag": "GPS"},  # 未配置坐标
        ]}}


def test_wvp_login_devices_and_geo_sync(tmp_path):
    fake = FakeHttp()
    c = WvpClient("http://127.0.0.1:18080", "admin", "pw", http=lambda url: fake.get(url),
                  )
    # 注入:post 与 get 分开
    c._post = fake.post
    c.devices()
    assert c._token == "T123"
    assert any("/api/device/query/devices" in u for u in fake.gets)
    geo = GeoStore(tmp_path / "geo.db")
    from visual.geo import sync_from_device_list
    devs = c.devices()
    synced = sync_from_device_list(devs, geo)
    assert len(synced) == 1  # 无坐标的第二台被跳过
    assert synced[0]["id"] == "34020000001320000001" and synced[0]["name"] == "东门枪机"
    geo.close()


def test_wvp_play_url_uses_token(tmp_path):
    calls = []
    fake = FakeHttp()
    c = WvpClient("http://wvp", "admin", "pw")
    c._token = "T123"
    c._get = lambda url: (calls.append(url), {"data": {"rtsp": "rtsp://zlm/x", "flv": "http://f.flv",
                                                       "hls": "http://h.m3u8"}})[1]
    out = c.play_url("DEV1", "CH1")
    assert out["rtsp"] == "rtsp://zlm/x" and "play/start/DEV1/CH1" in calls[0]


def test_wvp_reads_all_pages_and_preserves_equator_coordinates():
    from urllib.parse import urlsplit, parse_qs
    c = WvpClient("http://wvp", "admin", "pw"); c._token = "local-test"
    def get(url):
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        return {"code": 0, "data": {"total": 2, "list": [
            {"deviceId": str(page), "latitude": "0", "longitude": "121"}]}}
    c._get = get
    rows = c.devices()
    assert len(rows) == 2 and rows[0]["lat"] == 0 and rows[0]["lng"] == 121

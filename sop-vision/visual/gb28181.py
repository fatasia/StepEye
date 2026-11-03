"""GB28181 适配器:wvp-GB28181-pro HTTP API 客户端(设备目录含经纬度、流地址)。

不自研 SIP 信令(纯 Python 无成熟全功能实现,见 ROADMAP);部署侧用 wvp-pro + ZLMediaKit
compose(见 README 部署章),平台侧只做 HTTP 拉取:
- 设备目录(带 longitude/latitude,国标必选)→ geo.sync_from_device_list 进 GIS;
- 点播流地址(RTSP/FLV)→ 供工位 source 配置。

环境变量(缺省禁用,端点返回明示):
  GB28181_WVP_URL   如 http://127.0.0.1:18080(wvp-pro 地址)
  GB28181_USER / GB28181_PASS
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable


class _Http:
    def post(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={**(headers or {}), "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 —— url 来自部署配置
            return json.load(r)

    def get(self, url: str) -> dict:
        with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310
            return json.load(r)


class WvpClient:
    """wvp-GB28181-pro REST 客户端;transport 可注入用于离线测试。"""

    def __init__(self, base_url: str, username: str, password: str, http: Callable | None = None):
        self.base = base_url.rstrip("/")
        self.username, self.password = username, password
        self._post = http or _Http().post
        self._get = http or _Http().get
        self._token: str | None = None

    def _api(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{self.base}{path}{sep}access-token={self._token}"

    def login(self) -> None:
        resp = self._post(f"{self.base}/api/user/login",
                          {"username": self.username, "password": self.password})
        self._token = resp.get("data", {}).get("accessToken")
        if not self._token:
            raise RuntimeError("wvp 登录失败:未返回 accessToken")

    def devices(self) -> list[dict[str, Any]]:
        """设备目录 → 统一形态 [{id, name, lat, lng, source}]。wvp 真机联调待验证。"""
        if not self._token:
            self.login()
        resp = self._get(self._api("/api/device/query/devices?page=1&count=1000"))
        out = []
        for d in resp.get("data", {}).get("list", []):
            lat = d.get("longitude") if d.get("longitudeTag") == "GPS" else None
            lng = d.get("latitude") if d.get("longitudeTag") == "GPS" else None
            # 国标常见字段互换:多数实现 longitude=经度;个别实现字段互换,联调时以真机为准
            lat, lng = float(d.get("latitude") or 0) or None, float(d.get("longitude") or 0) or None
            out.append({"id": str(d.get("deviceId")), "name": str(d.get("name") or d.get("deviceId")),
                        "lat": lat, "lng": lng, "source": "gb28181"})
        return out

    def play_url(self, device_id: str, channel_id: str) -> dict[str, Any]:
        if not self._token:
            self.login()
        resp = self._get(self._api(f"/api/play/start/{device_id}/{channel_id}"))
        data = resp.get("data", {})
        return {"rtsp": data.get("rtsp"), "flv": data.get("flv"), "hls": data.get("hls")}

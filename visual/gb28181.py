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
import hashlib
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode
from typing import Any, Callable


class _Http:
    def post(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        req = urllib.request.Request(url, data=urlencode(payload).encode("utf-8"),
                                     headers={**(headers or {}), "Content-Type": "application/x-www-form-urlencoded"})
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
        # WVP 官方 UserController 使用 @RequestParam，密码须为32位MD5，不接JSON明文。
        resp = self._post(f"{self.base}/api/user/login",
                          {"username": self.username, "password": hashlib.md5(self.password.encode()).hexdigest()})
        data = resp.get("data", resp)
        self._token = data.get("accessToken") if isinstance(data, dict) else None
        if not self._token:
            raise RuntimeError("wvp 登录失败:未返回 accessToken")

    @staticmethod
    def _check_response(resp: dict) -> None:
        if resp.get("code") not in (None, 0, 200):
            raise RuntimeError("WVP 返回业务错误，请检查接口版本、会话与权限")

    def devices(self) -> list[dict[str, Any]]:
        """设备目录 → 统一形态 [{id, name, lat, lng, source}]。wvp 真机联调待验证。"""
        if not self._token:
            self.login()
        all_devices = []
        for page in range(1, 101):
            resp = self._get(self._api(f"/api/device/query/devices?page={page}&count=1000"))
            self._check_response(resp)
            data = resp.get("data", {})
            rows = data.get("list") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError("WVP 设备目录结构不匹配，请核对版本")
            all_devices.extend(rows)
            total = data.get("total")
            if total is not None and len(all_devices) >= int(total):
                break
            if not rows:
                if total is not None and len(all_devices) < int(total):
                    raise RuntimeError("WVP 分页结果不完整，未同步部分目录")
                break
            if total is None and len(rows) < 1000:
                break
        else:
            raise RuntimeError("WVP 设备目录超过 100 页安全上限，请按站点拆分同步")
        out = []
        for d in all_devices:
            if not isinstance(d, dict) or not d.get("deviceId"):
                raise RuntimeError("WVP 设备目录缺少 deviceId，未同步部分目录")
            try:
                lat = float(d["latitude"]) if d.get("latitude") not in (None, "") else None
                lng = float(d["longitude"]) if d.get("longitude") not in (None, "") else None
                if (lat == lng == 0) or lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    lat, lng = None, None
            except (ValueError, TypeError):
                lat, lng = None, None
            out.append({"id": str(d.get("deviceId")), "name": str(d.get("name") or d.get("deviceId")),
                        "lat": lat, "lng": lng, "source": "gb28181"})
        return out

    def play_url(self, device_id: str, channel_id: str) -> dict[str, Any]:
        if not self._token:
            self.login()
        resp = self._get(self._api(f"/api/play/start/{quote(device_id, safe='')}/{quote(channel_id, safe='')}"))
        self._check_response(resp)
        data = resp.get("data", {})
        return {"rtsp": data.get("rtsp"), "flv": data.get("flv"), "hls": data.get("hls")}

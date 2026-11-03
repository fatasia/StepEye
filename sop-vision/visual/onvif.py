"""ONVIF 发现与接入：管理网白名单、DNS固定与同设备服务地址校验。

默认只允许 RFC1918 / IPv6 ULA。回环、链路本地或授权公网测试须在部署侧
ONVIF_ALLOWED_CIDRS 显式追加；本模块不扫描公网摄像头。
"""
from __future__ import annotations

import ipaddress
import math
import os
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class OnvifPolicyError(RuntimeError):
    """可返回给操作员的、已脱敏的地址策略错误。"""


DEFAULT_CIDRS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")


def allowed_networks() -> tuple:
    from .llm_provider import _default_env_path, load_env_file
    values = load_env_file(_default_env_path())
    extra = os.environ.get("ONVIF_ALLOWED_CIDRS", values.get("ONVIF_ALLOWED_CIDRS", ""))
    try:
        return tuple(ipaddress.ip_network(item, strict=False) for item in
                     [*DEFAULT_CIDRS, *filter(None, re.split(r"[,\s]+", extra.strip()))])
    except ValueError:
        raise OnvifPolicyError("ONVIF_ALLOWED_CIDRS 配置无效，须为明确的 IP 网段") from None


def _host(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 253 or any(ord(ch) <= 32 for ch in value):
        raise OnvifPolicyError("ONVIF 主机须为 IP 或合法主机名，不能包含 URL/空白")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if any(ch in value for ch in "/\\@?#%"):
        raise OnvifPolicyError("ONVIF 主机不能包含 URL、路径、凭据或作用域标记")
    try:
        address = ipaddress.ip_address(value)
        return str(address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else str(address)
    except ValueError:
        pass
    try:
        name = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise OnvifPolicyError("ONVIF 主机名无效") from None
    if not name or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in name.split(".")):
        raise OnvifPolicyError("ONVIF 主机名无效，不接受 URL 或嵌入端口")
    return name


def _port(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise OnvifPolicyError("ONVIF 端口须为 1–65535 的整数")
    return value


def _addresses(host: str, port: int, resolver=None) -> tuple[str, ...]:
    try:
        return (str(ipaddress.ip_address(host)),)
    except ValueError:
        pass
    try:
        rows = (resolver or socket.getaddrinfo)(host, port, type=socket.SOCK_STREAM)
        addresses = set()
        for row in rows:
            address = ipaddress.ip_address(row[4][0])
            addresses.add(str(address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else str(address))
        if not addresses:
            raise ValueError
        return tuple(sorted(addresses, key=lambda value: (ipaddress.ip_address(value).version, value)))
    except (OSError, ValueError, IndexError, TypeError):
        raise OnvifPolicyError("无法解析 ONVIF 设备主机") from None


@dataclass(frozen=True)
class DeviceTarget:
    host: str
    port: int
    ip: str


def validate_device_target(host: str, port: int, *, networks: tuple | None = None,
                           resolver=None, resolve_dns: bool = True) -> DeviceTarget:
    """解析全部地址并逐一授权，再选择固定 IP；注入替身可关闭真实 DNS。"""
    host, port = _host(host), _port(port)
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if not resolve_dns and not literal:
        return DeviceTarget(host, port, host)  # 仅 transport_factory 替身路径，不发真实网络请求
    addresses = _addresses(host, port, resolver)
    networks = allowed_networks() if networks is None else networks
    for value in addresses:
        ip = ipaddress.ip_address(value)
        if ip.is_unspecified or ip.is_multicast or not any(ip.version == net.version and ip in net for net in networks):
            raise OnvifPolicyError("ONVIF 设备地址不在允许的管理网段；测试网须显式配置 ONVIF_ALLOWED_CIDRS")
    return DeviceTarget(host, port, addresses[0])


def pin_service_xaddr(value: str, target: DeviceTarget, *, resolver=None) -> str:
    """设备只能声明同一固定 IP 的 HTTP(S) 服务；返回值中的主机也固定为 IP。"""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError
        host = _host(parsed.hostname)
        port = _port(parsed.port or (443 if parsed.scheme == "https" else 80))
        if host not in (target.host, target.ip):
            if set(_addresses(host, port, resolver)) != {target.ip}:
                raise ValueError
        authority = f"[{target.ip}]" if ":" in target.ip else target.ip
        if parsed.port is not None:
            authority += f":{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))
    except (ValueError, TypeError, OnvifPolicyError):
        raise OnvifPolicyError("设备声明了不允许的 ONVIF 服务地址，未继续访问") from None


class _DeviceSession:
    """限制库实际 SOAP 请求；不允许 HTTP 重定向绕过固定 IP 校验。"""
    def __init__(self, session, target: DeviceTarget):
        self._session, self._target = session, target

    def _request(self, method: str, url: str, *args, **kwargs):
        url = pin_service_xaddr(str(url), self._target)
        kwargs["allow_redirects"] = False
        kwargs["proxy"] = None
        return getattr(self._session, method)(url, *args, **kwargs)

    def post(self, url: str, *args, **kwargs):
        return self._request("post", url, *args, **kwargs)

    def get(self, url: str, *args, **kwargs):
        return self._request("get", url, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._session, name)


def _guard_camera(cam, target: DeviceTarget) -> None:
    # onvif-zeep-async 4.3.0：update_xaddrs 仅在固定 devicemgmt 上 GetServices /
    # GetCapabilities 后填充 xaddrs，不请求发现的服务；仍需防初始请求的重定向。
    # 每个服务 setup 只加载随库附带的本地 WSDL，实际 SOAP 调用前包裹其 session。
    original = cam.create_onvif_service

    async def create_service(*args, **kwargs):
        service = await original(*args, **kwargs)
        session = getattr(getattr(service, "transport", None), "session", None)
        if session is None:
            raise OnvifPolicyError("当前 ONVIF 库不支持受限传输，请核对依赖版本")
        if not isinstance(session, _DeviceSession):
            service.transport.session = _DeviceSession(session, target)
        return service

    cam.create_onvif_service = create_service
    cam.rewrite_url = lambda value: pin_service_xaddr(value, target)


def wsdiscovery_probe(timeout_s: float = 3.0) -> list[dict[str, Any]]:
    """WS-Discovery 多播发现局域网 ONVIF 设备。

    返回 [{address(ONVIF 服务地址), scopes, xaddrs}, ...]。需要 pip install wsdiscovery。
    """
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or not 1 <= timeout_s <= 15:
        raise OnvifPolicyError("ONVIF 发现时长须为 1–15 秒")
    try:
        from wsdiscovery import WSDiscovery
    except ImportError:
        raise RuntimeError("缺少依赖: pip install wsdiscovery") from None
    wsd = WSDiscovery()
    try:
        wsd.start()
        services = wsd.searchServices(timeout=int(math.ceil(timeout_s)))
        out = []
        for s in services:
            xaddrs = s.getXAddrs()
            scopes = [str(sc) for sc in s.getScopes()]
            out.append({"xaddr": xaddrs[0] if xaddrs else "", "scopes": scopes})
        return out
    finally:
        wsd.stop() if hasattr(wsd, "stop") else None


def device_rtsp_and_geo(host: str, port: int, user: str, password: str,
                        transport_factory=None) -> dict[str, Any]:
    """读设备信息:RTSP 主码流地址 + GeoLocation(若有)。

    依赖 onvif-zeep-async(Home Assistant 同款);async 库,这里用 asyncio.run 包同步。
    transport_factory 仅供测试注入(替换 ONVIFService 客户端构造)。
    """
    injected = transport_factory is not None
    target = validate_device_target(host, port, resolve_dns=not injected)
    if not injected:
        try:
            from onvif import ONVIFCamera  # onvif-zeep-async
        except ImportError:
            raise RuntimeError("缺少依赖: pip install onvif-zeep-async") from None
        transport_factory = ONVIFCamera
    import asyncio

    async def _run() -> dict[str, Any]:
        cam = transport_factory(target.ip, target.port, user, password)
        try:
            if not injected:
                _guard_camera(cam, target)
            await cam.update_xaddrs()
            xaddrs = getattr(cam, "xaddrs", None)
            if xaddrs is None and not injected:
                raise OnvifPolicyError("当前 ONVIF 库未提供可校验的服务地址")
            if xaddrs is not None:
                if not isinstance(xaddrs, dict):
                    raise OnvifPolicyError("设备 ONVIF 服务地址格式无效")
                cam.xaddrs = {key: pin_service_xaddr(value, target) for key, value in xaddrs.items()}
            media = await cam.create_media_service()
            profiles = await media.GetProfiles()
            stream_url = None
            if profiles:
                uri = await media.GetStreamUri({"StreamSetup": {"Stream": "RTP-Unicast",
                                                                  "Transport": {"Protocol": "RTSP"}},
                                                "ProfileToken": profiles[0].token})
                stream_url = uri.Uri
            geo = None
            try:
                dev = await cam.create_devicemgmt_service()
                geo = await dev.GetGeoLocation()
            except Exception:  # noqa: BLE001 —— GeoLocation 可选,老固件没有
                pass
            lat = float(geo[0].Lat) if geo and getattr(geo[0], "Lat", None) is not None else None
            lng = float(geo[0].Lon) if geo and getattr(geo[0], "Lon", None) is not None else None
            if lat is None or lng is None or not math.isfinite(lat) or not math.isfinite(lng) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
                lat = lng = None
            return {"rtsp": stream_url, "lat": lat, "lng": lng}
        finally:
            await cam.close()

    async def bounded():
        return await asyncio.wait_for(_run(), timeout=20)
    try:
        return asyncio.run(bounded())
    except OnvifPolicyError:
        raise
    except Exception:
        raise RuntimeError("ONVIF 读取失败：请检查地址、端口、账户及设备协议支持") from None

"""ONVIF 发现与设备接入(轻量包装,协议全靠现成库;可选依赖,缺失时明示)。"""
from __future__ import annotations

import time
from typing import Any


def wsdiscovery_probe(timeout_s: float = 3.0) -> list[dict[str, Any]]:
    """WS-Discovery 多播发现局域网 ONVIF 设备。

    返回 [{address(ONVIF 服务地址), scopes, xaddrs}, ...]。需要 pip install wsdiscovery。
    """
    try:
        from wsdiscovery import WSDiscovery
    except ImportError:
        raise RuntimeError("缺少依赖: pip install wsdiscovery") from None
    wsd = WSDiscovery()
    t0 = time.monotonic()
    wsd.start()
    try:
        services = wsd.searchServices(timeout=int(max(1, timeout_s)))
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
    try:
        from onvif import ONVIFCamera  # onvif-zeep-async
    except ImportError:
        raise RuntimeError("缺少依赖: pip install onvif-zeep-async") from None
    import asyncio

    async def _run() -> dict[str, Any]:
        cam = ONVIFCamera(host, port, user, password)
        await cam.update_xaddrs()
        media = cam.create_media_service()
        profiles = await media.GetProfiles()
        stream_url = None
        if profiles:
            tok = profiles[0].token
            req = media.create_type("GetStreamUri", {"StreamSetup": {"Stream": "RTP-Unicast",
                                                                     "Transport": {"Protocol": "RTSP"}},
                                                     "ProfileToken": tok})
            uri = await media.GetStreamUri(req)
            stream_url = uri.Uri
        geo = None
        try:
            dev = cam.create_devicemgmt_service()
            geo = await dev.GetGeoLocation()
        except Exception:  # noqa: BLE001 —— GeoLocation 可选,老固件没有
            geo = None
        lat = float(geo[0].Lat) if geo and getattr(geo[0], "Lat", None) is not None else None
        lng = float(geo[0].Lon) if geo and getattr(geo[0], "Lon", None) is not None else None
        await cam.close()
        return {"rtsp": stream_url, "lat": lat, "lng": lng}

    return asyncio.run(_run())

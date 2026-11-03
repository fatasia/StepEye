"""生产地址/资源边界反例回归；只使用替身和临时文件，不访问真实设备。"""
from __future__ import annotations

import ipaddress
import socket
import sys
from types import SimpleNamespace

import pytest

from visual.onvif import (DeviceTarget, OnvifPolicyError, _DeviceSession, allowed_networks,
                          device_rtsp_and_geo, pin_service_xaddr, validate_device_target,
                          wsdiscovery_probe)


@pytest.fixture(autouse=True)
def clean_onvif_policy(monkeypatch):
    monkeypatch.setenv("ONVIF_ALLOWED_CIDRS", "")  # 不继承本机测试网或真实 .env


@pytest.mark.parametrize("host", ["10.1.2.3", "172.16.0.8", "192.168.1.20", "fd12::20"])
def test_onvif_private_management_networks_are_allowed(host):
    target = validate_device_target(host, 80)
    assert target.ip == str(ipaddress.ip_address(host))


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "8.8.8.8", "0.0.0.0", "224.0.0.1",
                                  "::1", "fe80::1", "::ffff:127.0.0.1"])
def test_onvif_loopback_metadata_public_and_multicast_are_not_default_targets(host):
    with pytest.raises(OnvifPolicyError):
        validate_device_target(host, 80)


def test_onvif_explicit_lab_or_authorized_public_cidr_is_required(monkeypatch):
    monkeypatch.setenv("ONVIF_ALLOWED_CIDRS", "127.0.0.0/8, 8.8.8.8/32, ::1/128")
    assert validate_device_target("127.0.0.1", 18880).ip == "127.0.0.1"
    assert validate_device_target("8.8.8.8", 80).ip == "8.8.8.8"
    assert validate_device_target("[::1]", 80).ip == "::1"


@pytest.mark.parametrize("host", ["http://192.168.1.20", "cam:8080", "user@cam", "cam/path", "cam?x=1",
                                  "cam#fragment", "cam\\path", "cam%2fpath", " cam", "cam\n", "-camera.local"])
def test_onvif_host_is_not_an_arbitrary_url(host):
    with pytest.raises(OnvifPolicyError):
        validate_device_target(host, 80, resolve_dns=False)


@pytest.mark.parametrize("port", [0, -1, 65536, 80.5, True, "80"])
def test_onvif_port_is_bounded_integer(port):
    with pytest.raises(OnvifPolicyError):
        validate_device_target("192.168.1.20", port)


def _dns(*ips):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 80)) for ip in ips]


def test_onvif_every_dns_result_must_be_allowed_and_selected_ip_is_fixed():
    target = validate_device_target("camera.lab", 80, resolver=lambda *args, **kwargs: _dns("192.168.1.20", "192.168.1.21"))
    assert target.host == "camera.lab" and target.ip == "192.168.1.20"
    with pytest.raises(OnvifPolicyError):
        validate_device_target("camera.lab", 80, resolver=lambda *args, **kwargs: _dns("192.168.1.20", "8.8.8.8"))
    # 已验证的原主机直接映射固定IP，不给第二次DNS重绑定机会。
    def unexpected_dns(*args, **kwargs):
        raise AssertionError("original hostname should not be resolved again")
    assert pin_service_xaddr("http://camera.lab:8080/onvif/media", target, resolver=unexpected_dns) == "http://192.168.1.20:8080/onvif/media"


@pytest.mark.parametrize("url", ["http://192.168.1.21/onvif/media", "http://127.0.0.1/onvif/media",
                                 "file:///etc/passwd", "ftp://192.168.1.20/media",
                                 "http://user:pass@192.168.1.20/onvif/media", "http://192.168.1.20:99999/media"])
def test_onvif_returned_service_cannot_target_another_device_or_protocol(url):
    target = DeviceTarget("camera.lab", 80, "192.168.1.20")
    with pytest.raises(OnvifPolicyError):
        pin_service_xaddr(url, target)


def test_onvif_session_pins_actual_requests_and_disables_redirects_and_proxies():
    calls = []
    class Session:
        def post(self, url, **kwargs):
            calls.append((url, kwargs)); return "response"
    guard = _DeviceSession(Session(), DeviceTarget("camera.lab", 80, "192.168.1.20"))
    assert guard.post("http://camera.lab/onvif/device_service", allow_redirects=True, proxy="http://unwanted-proxy") == "response"
    assert calls[0][0] == "http://192.168.1.20/onvif/device_service"
    assert calls[0][1]["allow_redirects"] is False and calls[0][1]["proxy"] is None


@pytest.mark.parametrize("timeout", [0, 16, float("inf"), float("nan"), True])
def test_onvif_discovery_timeout_is_finite_and_bounded_before_starting_network(timeout):
    with pytest.raises(OnvifPolicyError):
        wsdiscovery_probe(timeout)


def test_onvif_env_overrides_dotenv_for_allowed_networks(tmp_path, monkeypatch):
    import visual.llm_provider as provider
    env = tmp_path / ".env"; env.write_text("ONVIF_ALLOWED_CIDRS=127.0.0.0/8\n", encoding="utf-8")
    monkeypatch.setattr(provider, "_default_env_path", lambda: env)
    monkeypatch.delenv("ONVIF_ALLOWED_CIDRS")
    assert ipaddress.ip_network("127.0.0.0/8") in allowed_networks()
    monkeypatch.setenv("ONVIF_ALLOWED_CIDRS", "8.8.8.8/32")
    assert ipaddress.ip_network("127.0.0.0/8") not in allowed_networks()
    assert ipaddress.ip_network("8.8.8.8/32") in allowed_networks()


def test_onvif_library_path_uses_pinned_ip_and_guarded_service_transport(monkeypatch):
    calls, constructed = [], []
    class Session:
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
    class Service:
        def __init__(self): self.transport = SimpleNamespace(session=Session())
        async def GetProfiles(self): return []
        async def GetGeoLocation(self): return []
    class Camera:
        def __init__(self, host, port, user, password):
            constructed.append((host, port)); self.xaddrs = {}; self.closed = False
        async def create_onvif_service(self, name): return Service()
        async def create_devicemgmt_service(self): return await self.create_onvif_service("device")
        async def create_media_service(self): return await self.create_onvif_service("media")
        async def update_xaddrs(self):
            service = await self.create_devicemgmt_service()
            await service.transport.session.post("http://camera.lab/onvif/device_service")
            self.xaddrs = {"media": "http://camera.lab/onvif/media"}
        async def close(self): self.closed = True
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _dns("192.168.1.20"))
    monkeypatch.setitem(sys.modules, "onvif", SimpleNamespace(ONVIFCamera=Camera))
    assert device_rtsp_and_geo("camera.lab", 80, "u", "unused")["rtsp"] is None
    assert constructed == [("192.168.1.20", 80)]
    assert calls[0][0] == "http://192.168.1.20/onvif/device_service" and not calls[0][1]["allow_redirects"]


def test_onvif_bad_xaddr_is_rejected_before_media_service_and_camera_is_closed():
    class Camera:
        closed = False
        def __init__(self, *args): self.xaddrs = {"media": "http://127.0.0.1/onvif/media"}
        async def update_xaddrs(self): pass
        async def create_media_service(self): raise AssertionError("must reject before media request")
        async def close(self): type(self).closed = True
    with pytest.raises(OnvifPolicyError):
        device_rtsp_and_geo("camera.lab", 80, "u", "unused", Camera)
    assert Camera.closed


def test_onvif_nonfinite_coordinates_are_unknown_not_json_nan():
    class Camera:
        def __init__(self, *args): pass
        async def update_xaddrs(self): pass
        async def create_media_service(self): return self
        async def GetProfiles(self): return []
        async def create_devicemgmt_service(self): return self
        async def GetGeoLocation(self): return [SimpleNamespace(Lat=float("nan"), Lon=121)]
        async def close(self): pass
    result = device_rtsp_and_geo("camera.lab", 80, "u", "unused", Camera)
    assert result["lat"] is None and result["lng"] is None

from types import SimpleNamespace

import pytest

from visual.onvif import device_rtsp_and_geo


class FakeCamera:
    fail = False
    closed = False

    def __init__(self, *args):
        type(self).closed = False

    async def update_xaddrs(self):
        if self.fail:
            raise OSError("secret must not appear in response")

    async def create_media_service(self):
        return self

    async def GetProfiles(self):
        return [SimpleNamespace(token="main")]

    async def GetStreamUri(self, req):
        assert req["ProfileToken"] == "main"
        return SimpleNamespace(Uri="rtsp://lab/main")

    async def create_devicemgmt_service(self):
        return self

    async def GetGeoLocation(self):
        raise NotImplementedError

    async def close(self):
        type(self).closed = True


def test_async_services_are_awaited_and_optional_geo_is_null():
    out = device_rtsp_and_geo("lab", 80, "u", "p", FakeCamera)
    assert out == {"rtsp": "rtsp://lab/main", "lat": None, "lng": None}
    assert FakeCamera.closed


def test_camera_closes_on_error_and_exception_is_sanitized():
    class Failed(FakeCamera):
        fail = True
    with pytest.raises(RuntimeError) as e:
        device_rtsp_and_geo("lab", 80, "u", "p", Failed)
    assert Failed.closed and "secret" not in str(e.value)

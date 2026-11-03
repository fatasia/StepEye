from __future__ import annotations

import time
import threading
from typing import Protocol

import cv2
import numpy as np


class FrameSource(Protocol):
    fps: float

    def read(self) -> tuple[float, np.ndarray] | None: ...
    def close(self) -> None: ...


class CvSource:
    """文件 / 摄像头 / RTSP。文件用视频内时间戳；实时流用单调时钟，并在断流后自动重连。

    stop_event: 运行时停止信号——重连循环每轮检查,保证 stop() 能真正停下坏 RTSP 源(否则关服务会卡死)。
    """

    def __init__(self, kind: str, uri: str | int | None, fps: float = 25.0, realtime: bool = True,
                 loop: bool = False, size: tuple[int, int] | None = None,
                 stop_event: threading.Event | None = None):
        self.kind, self.uri, self.loop, self.realtime = kind, uri, loop, realtime
        self.fps = fps
        self.size = size
        self.stop_event = stop_event
        self._cap: cv2.VideoCapture | None = None
        self._t0 = time.monotonic()
        self._n = 0
        self._open()

    def _open(self) -> None:
        if self.kind == "rtsp" and not str(self.uri or "").lower().startswith(
                ("rtsp://", "rtmp://", "http://", "https://", "file://")):
            raise ValueError(f"无效的 RTSP 地址:{self.uri!r}(占位符未替换或格式错误)")  # fail-fast:坏地址不进重连循环
        if self._cap is not None:
            self._cap.release()
        uri = int(self.uri) if self.kind == "camera" else self.uri
        self._cap = cv2.VideoCapture(uri)
        if self.kind == "camera" and self.size:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
        f = self._cap.get(cv2.CAP_PROP_FPS)
        if self.kind == "file" and f and f > 1:
            self.fps = f

    def read(self) -> tuple[float, np.ndarray] | None:
        assert self._cap is not None
        ok, frame = self._cap.read()
        if not ok:
            if self.kind == "file":
                if not self.loop:
                    return None
                self._open()
                self._n = 0
                return self.read()
            if self.stop_event is not None and self.stop_event.is_set():
                return None  # 停止信号:不再重连,让运行线程退出
            if self.stop_event is not None:
                self.stop_event.wait(1.0)  # 实时流断开:退避后重连(等待可被 stop 打断)
            else:
                time.sleep(1.0)
            self._open()
            if self.stop_event is not None and self.stop_event.is_set():
                return None
            return self.read() if self._cap.isOpened() else (time.monotonic() - self._t0, np.zeros((2, 2, 3), np.uint8))
        if self.kind == "file":
            ts = self._n / self.fps
            self._n += 1
            if self.realtime:
                delay = self._t0 + ts - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        else:
            ts = time.monotonic() - self._t0
        return ts, frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()


def create_source(config, stop_event=None, base_dir=None):
    """Keep optional camera SDK outside ordinary OpenCV source imports."""
    if config.type == "industrial":
        from .industrial_camera import IndustrialCamera
        return IndustrialCamera(config.industrial, config.fps, stop_event, base_dir)
    return CvSource(config.type, config.uri, config.fps, config.realtime, config.loop,
                    (config.width, config.height), stop_event=stop_event)

from __future__ import annotations

import time
from typing import Protocol

import cv2
import numpy as np


class FrameSource(Protocol):
    fps: float

    def read(self) -> tuple[float, np.ndarray] | None: ...
    def close(self) -> None: ...


class CvSource:
    """文件 / 摄像头 / RTSP。文件用视频内时间戳；实时流用单调时钟，并在断流后自动重连。"""

    def __init__(self, kind: str, uri: str | int | None, fps: float = 25.0, realtime: bool = True,
                 loop: bool = False, size: tuple[int, int] | None = None):
        self.kind, self.uri, self.loop, self.realtime = kind, uri, loop, realtime
        self.fps = fps
        self.size = size
        self._cap: cv2.VideoCapture | None = None
        self._t0 = time.monotonic()
        self._n = 0
        self._open()

    def _open(self) -> None:
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
            time.sleep(1.0)  # 实时流断开：退避后重连
            self._open()
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

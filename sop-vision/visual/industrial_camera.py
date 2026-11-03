"""Optional GenTL acquisition through Harvester; imported/opened only on demand."""
from __future__ import annotations

from pathlib import Path
import threading
import time

import cv2
import numpy as np


class IndustrialCamera:
    def __init__(self, config, fps=25., stop_event=None, base_dir=None):
        self.config, self.fps = config, fps
        self.stop_event = stop_event or threading.Event()
        self.base_dir = Path(base_dir or '.')
        self._lock = threading.RLock()
        self._trigger_lock = threading.Lock()
        self._trigger_pending = False
        self._harvester = self._acquirer = self._genapi = None
        self._closed = False
        self._started = False
        self._t0 = time.monotonic()
        self.last_metadata = {}

    def _open(self):
        if self._closed:
            raise RuntimeError("工业相机已关闭")
        if self._acquirer is not None:
            return
        path = Path(self.config.cti)
        if not path.is_absolute():
            path = self.base_dir / path
        path = path.resolve()
        if path.suffix.lower() != '.cti' or not path.is_file():
            raise ValueError("本机 GenTL CTI 文件不存在")
        try:
            from harvesters.core import Harvester
            from genicam import genapi
        except ImportError as exc:
            raise RuntimeError("工业相机需要可选 requirements-camera.txt 与厂商 GenTL CTI") from exc
        self._genapi = genapi
        try:
            self._harvester = Harvester()
            self._harvester.add_file(str(path))
            self._harvester.update()
            self._acquirer = self._harvester.create({'serial_number': self.config.serial_number})
            if self.config.exposure_us is not None:
                self._manual('ExposureAuto', 'ExposureTime', self.config.exposure_us)
            if self.config.gain is not None:
                self._manual('GainAuto', 'Gain', self.config.gain)
            # Explicit free-run or software-trigger mode; never inherit a hidden external trigger.
            self._set('TriggerSelector', 'FrameStart')
            self._set('TriggerMode', 'Off')
            if self.config.trigger == 'software':
                self._set('TriggerSource', 'Software')
                self._set('TriggerMode', 'On')
                self._node('TriggerSoftware', write=True)
            self._acquirer.start()
            self._started = True
        except Exception:
            self._release()
            raise

    def _node(self, name, write=False):
        try:
            node = getattr(self._acquirer.remote_device.node_map, name)
            allowed = self._genapi.is_writable(node) if write else self._genapi.is_readable(node)
            if not allowed:
                raise ValueError("节点不可写" if write else "节点不可读")
            return node
        except Exception as exc:
            raise ValueError(f"工业相机不支持所需能力: {name}") from exc

    def _set(self, name, value):
        node = self._node(name, write=True)
        node.value = value
        actual = self._node(name).value
        if isinstance(value, (int, float)):
            if not np.isclose(float(actual), value, rtol=1e-6, atol=1e-6):
                raise ValueError(f"{name} 写入后读回不一致")
        elif actual != value:
            raise ValueError(f"{name} 写入后读回不一致")
        return actual

    def _manual(self, auto, name, value):
        # A missing Auto node is acceptable only if manual control is readable/writable.
        try:
            getattr(self._acquirer.remote_device.node_map, auto)
        except AttributeError:
            pass
        else:
            self._set(auto, 'Off')
        return self._set(name, value)

    def capabilities(self):
        with self._lock:
            if self._acquirer is None:
                return {'opened': False, 'serial_number': self.config.serial_number,
                        'reason': '等待工位首次采集，不为查询启动设备'}
            result = {'opened': True, 'serial_number': self.config.serial_number, 'nodes': {},
                      'last_frame': dict(self.last_metadata)}
            for name in ('ExposureTime', 'ExposureAuto', 'Gain', 'GainAuto', 'TriggerSelector',
                         'TriggerMode', 'TriggerSource', 'TriggerSoftware', 'PixelFormat'):
                try:
                    node = getattr(self._acquirer.remote_device.node_map, name)
                    readable = bool(self._genapi.is_readable(node))
                    result['nodes'][name] = {'readable': readable, 'writable': bool(self._genapi.is_writable(node)),
                                              'value': node.value if readable and name != 'TriggerSoftware' else None}
                except Exception:
                    result['nodes'][name] = {'readable': False, 'writable': False, 'value': None}
            return result

    def software_trigger(self):
        # Request thread never calls the camera SDK concurrently with fetch.
        # One bounded slot is consumed by the acquisition thread on its next poll.
        with self._trigger_lock:
            if self._closed or self.stop_event.is_set() or self.config.trigger != 'software' or not self._started:
                raise ValueError("相机未开启软件触发")
            if self._trigger_pending:
                raise RuntimeError("已有软件触发等待采集，不再排队")
            self._trigger_pending = True
        return {'queued': True}

    @staticmethod
    def _image(buffer):
        if len(buffer.payload.components) != 1:
            raise ValueError("仅支持单图像平面载荷")
        component = buffer.payload.components[0]
        width, height = int(component.width), int(component.height)
        if not (1 <= width <= 32768 and 1 <= height <= 32768 and width * height <= 50000000):
            raise ValueError("工业相机图像尺寸无效")
        data = component.data
        if data.dtype != np.uint8:
            raise ValueError("当前仅支持 8 位像素格式，不自动压缩高位深")
        fmt = component.data_format
        if fmt == 'Mono8':
            return cv2.cvtColor(data.reshape(height, width), cv2.COLOR_GRAY2BGR)
        if fmt in ('RGB8', 'BGR8'):
            frame = data.reshape(height, width, 3)
            return frame[:, :, ::-1].copy() if fmt == 'RGB8' else frame.copy()
        raise ValueError(f"不支持像素格式: {fmt}")

    def read(self):
        if self.stop_event.is_set() or self._closed:
            return None
        with self._lock:
            try:
                self._open()
                with self._trigger_lock:
                    trigger = self._trigger_pending
                    self._trigger_pending = False
                if trigger:
                    self._node('TriggerSoftware', write=True).execute()
                # Bounded fetch lets normal stop observe its event within 200 ms.
                buffer = self._acquirer.try_fetch(timeout=.2)
                if self.stop_event.is_set():
                    if buffer is not None:
                        buffer.queue()
                    return None
                if buffer is None:
                    return time.monotonic() - self._t0, np.zeros((2, 2, 3), np.uint8)
                with buffer:
                    frame = self._image(buffer)
                    self.last_metadata = {'hardware_timestamp_ns': getattr(buffer, 'timestamp_ns', None),
                                          'frame_id': getattr(buffer, 'frame_id', None),
                                          'timestamp_basis': 'host_monotonic_for_rules'}
                # Device ticks never enter SOP time: frequency/epoch vary across vendors.
                return time.monotonic() - self._t0, frame
            except Exception:
                self._release()
                raise

    def _release(self):
        acquirer, harvester = self._acquirer, self._harvester
        self._acquirer = self._harvester = None
        errors = []
        if acquirer is not None:
            if self._started:
                try:
                    acquirer.stop()
                except Exception as exc:
                    errors.append(exc)
            try:
                acquirer.destroy()
            except Exception as exc:
                errors.append(exc)
        self._started = False
        with self._trigger_lock:
            self._trigger_pending = False
        if harvester is not None:
            try:
                harvester.reset()
            except Exception as exc:
                errors.append(exc)
        if errors:
            self.last_metadata['release_errors'] = [type(exc).__name__ for exc in errors]

    def close(self):
        self.stop_event.set()
        with self._lock:
            self._closed = True
            self._release()

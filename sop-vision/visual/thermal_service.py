"""Camera-independent thermal evaluation on admitted signals; no polling thread."""
from __future__ import annotations

import threading
import time

from .events import Event, SIGNAL
from .thermal import ThermalCfg, ThermalMonitor


class ThermalSignalService:
    def __init__(self, cfg: ThermalCfg, emit, *, wall_clock=time.time,
                 monotonic=time.monotonic):
        self.enabled = cfg.enabled
        self.keys = frozenset((r.source, r.name) for r in cfg.rules) if cfg.enabled else frozenset()
        self._monitor = ThermalMonitor(cfg)
        self._emit = emit
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._closed = False

    def observe(self, packet, receipt):
        """Only inbox admission calls this. Snapshots never confirm a new sample."""
        if receipt['status'] != 'accepted' or (packet.source, packet.name) not in self.keys:
            return
        with self._lock:
            if self._closed:
                return
            ts = self._monotonic()
            event = Event(ts=ts, type=SIGNAL, name=packet.name, value=packet.value,
                          source=packet.source, conf=1. if packet.quality == 'ok' else 0.,
                          data={'sequence':packet.sequence, 'observed_at':packet.observed_at,
                                'received_at':receipt['received_at'], 'quality':packet.quality,
                                'audit_id':receipt['audit_id'],
                                'valid_until_ts':ts + receipt['expires_at'] - self._wall_clock()})
            alerts = self._monitor.process([event], ts)
        # Existing alert listeners may touch station state; do not call them under our lock.
        for alert in alerts:
            alert['clock'] = 'monotonic'
            self._emit(alert)

    def snapshot(self):
        with self._lock:
            if not self._closed:
                self._monitor.process([], self._monotonic())
            return self._monitor.snapshot()

    def close(self):
        with self._lock:
            self._closed = True
            self._monitor.reset('station_stopped')

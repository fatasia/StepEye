"""Explicit read-only Modbus TCP mappings -> authenticated StepEye signal inbox.

Run from sop-vision: python -m tools.modbus_signal_bridge --config bridge.json --once
Only functions 03/04 exist here. No device discovery, PLC writes or background service.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import struct
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$"
FORMATS = {"uint16": "H", "int16": "h", "uint32": "I", "int32": "i", "float32": "f"}
UNITS = {"temperature": {"C"}, "gas_concentration": {"ppm", "%LEL"}, "resistance": {"ohm", "Mohm"}}


class BridgeError(Exception):
    """Only stable error codes, never credentials or raw remote response bodies."""


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    source: str = Field(pattern=IDENTIFIER)
    name: str = Field(pattern=IDENTIFIER)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=502, ge=1, le=65535)
    unit_id: int = Field(ge=1, le=255)
    function: int = Field(ge=3, le=4)
    address: int = Field(ge=0, le=65535)
    datatype: str
    byte_order: str = "big"
    word_order: str = "big"
    quantity: str
    unit: str
    scale: float
    offset: float = 0.
    min_value: float
    max_value: float
    invalid_raw: list[int] = Field(default_factory=list, max_length=32)

    @property
    def count(self):
        return 1 if self.datatype in {"uint16", "int16"} else 2

    @model_validator(mode="after")
    def validate_mapping(self):
        if self.datatype not in FORMATS or self.byte_order not in {"big", "little"} or self.word_order not in {"big", "little"}:
            raise ValueError("explicit datatype/byte_order/word_order required")
        if self.quantity not in UNITS or self.unit not in UNITS[self.quantity]:
            raise ValueError("quantity and canonical unit do not match")
        if self.scale == 0 or self.min_value >= self.max_value or self.address + self.count > 65536:
            raise ValueError("invalid scale/range/register span")
        if self.quantity == "temperature" and not -273.15 <= self.min_value < self.max_value <= 4000:
            raise ValueError("temperature outside ThermalSample range")
        if self.quantity != "temperature" and self.min_value < 0:
            raise ValueError("gas/resistance range cannot be negative")
        if any(not 0 <= v < 2 ** (16 * self.count) for v in self.invalid_raw):
            raise ValueError("invalid_raw must be unsigned bit patterns for this datatype")
        if any(c.isspace() or c in "/@?#\\" for c in self.host):
            raise ValueError("host must not contain credentials, URL or whitespace")
        return self


class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    enabled: bool = False
    server: str = "http://127.0.0.1:8000"
    station: str = Field(default="", max_length=96)
    token_env: str = "STEPEYE_BRIDGE_TOKEN"
    interval_s: float = Field(default=1., ge=.1, le=3600)
    timeout_s: float = Field(default=2., ge=.1, le=10)
    ttl_ms: int = Field(default=5000, ge=100, le=600000)
    mappings: list[Mapping] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_config(self):
        parts = urlsplit(self.server)
        try: port = parts.port
        except ValueError: raise ValueError("invalid server") from None
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.path not in {"", "/"} or parts.query or parts.fragment
                or any(c.isspace() for c in self.server)):
            raise ValueError("server must be an origin without credentials/path/query")
        if parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("remote StepEye requires HTTPS")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", self.token_env):
            raise ValueError("invalid token_env name")
        if self.enabled and (not re.fullmatch(IDENTIFIER, self.station) or not self.mappings):
            raise ValueError("enabled bridge requires station and explicit mappings")
        if len({(m.source, m.name) for m in self.mappings}) != len(self.mappings):
            raise ValueError("duplicate source/name mapping")
        if self.enabled and self.ttl_ms <= (self.interval_s + self.timeout_s) * 1000:
            raise ValueError("TTL must exceed poll interval + one read timeout")
        return self


def read_registers(mapping: Mapping, timeout_s: float) -> bytes:
    """One bounded short connection; validate MBAP transaction/protocol/unit and PDU."""
    transaction = int.from_bytes(os.urandom(2), "big")
    request = struct.pack(">HHHBBHH", transaction, 0, 6, mapping.unit_id, mapping.function, mapping.address, mapping.count)
    deadline = time.monotonic() + timeout_s
    with socket.create_connection((mapping.host, mapping.port), timeout_s) as connection:
        connection.sendall(request)
        def receive(count):
            result = bytearray()
            while len(result) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise BridgeError("modbus_timeout")
                connection.settimeout(remaining)
                chunk = connection.recv(count - len(result))
                if not chunk: raise BridgeError("modbus_truncated")
                result.extend(chunk)
            return bytes(result)
        tid, protocol, length, unit = struct.unpack(">HHHB", receive(7))
        if (tid, protocol, unit) != (transaction, 0, mapping.unit_id) or not 2 <= length <= 254:
            raise BridgeError("modbus_header_mismatch")
        pdu = receive(length - 1)
        if pdu[0] == mapping.function | 0x80:
            if len(pdu) != 2: raise BridgeError("modbus_bad_exception")
            raise BridgeError(f"modbus_exception_{pdu[1]:02x}")
        if len(pdu) != 2 + mapping.count * 2 or pdu[:2] != bytes([mapping.function, mapping.count * 2]):
            raise BridgeError("modbus_payload_mismatch")
        return pdu[2:]


def decode(mapping: Mapping, raw: bytes) -> dict:
    if len(raw) != mapping.count * 2: raise BridgeError("register_size_mismatch")
    words = [raw[i:i + 2] for i in range(0, len(raw), 2)]
    if mapping.byte_order == "little": words = [word[::-1] for word in words]
    if mapping.word_order == "little": words.reverse()
    encoded = b"".join(words)
    if int.from_bytes(encoded, "big") in mapping.invalid_raw: raise BridgeError("device_invalid_sentinel")
    value = struct.unpack(">" + FORMATS[mapping.datatype], encoded)[0] * mapping.scale + mapping.offset
    if not math.isfinite(value) or not mapping.min_value <= value <= mapping.max_value:
        raise BridgeError("measurement_nonfinite_or_out_of_range")
    if mapping.quantity == "temperature": return {"temperature": float(value), "temperature_unit": "C"}
    return {"quantity": mapping.quantity, "value": float(value), "unit": mapping.unit}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class SequenceStore:
    """Single owner per state file; commit reserved sequence before sending, gaps are allowed."""
    def __init__(self, path: Path):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.with_suffix(path.suffix + ".lock").is_symlink():
            raise BridgeError("state_symlink_not_allowed")
        self._file = path.with_suffix(path.suffix + ".lock").open("a+b")
        self._file.seek(0); self._file.write(b"0"); self._file.flush(); self._file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file.close(); raise BridgeError("state_already_in_use") from None
        try:
            self._db = sqlite3.connect(path)
            self._db.execute("CREATE TABLE IF NOT EXISTS sequences (key TEXT PRIMARY KEY, sequence INTEGER NOT NULL)")
            self._db.commit()
        except Exception:
            self.close(); raise

    def next(self, key: str):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("SELECT sequence FROM sequences WHERE key=?", (key,)).fetchone()
            sequence = row[0] + 1 if row else 1
            if sequence > 2**63 - 1: raise BridgeError("sequence_exhausted")
            self._db.execute("INSERT OR REPLACE INTO sequences VALUES (?,?)", (key, sequence))
        return sequence

    def close(self):
        if getattr(self, "_db", None) is not None: self._db.close(); self._db = None
        if not self._file.closed: self._file.close()  # Closing releases flock/msvcrt lock.


class ModbusBridge:
    def __init__(self, config: BridgeConfig, state_path: Path):
        self.config, self._state, self._token = config, None, ""
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        if config.enabled:
            self._token = os.environ.get(config.token_env, "")
            if not self._token or any(c.isspace() for c in self._token): raise BridgeError("missing_or_invalid_session_token")
            self._state = SequenceStore(state_path)

    def _send(self, packet):
        cfg = self.config
        url = f"{cfg.server.rstrip('/')}/api/stations/{quote(cfg.station, safe='')}/signals"
        request = urllib.request.Request(url, data=json.dumps(packet, allow_nan=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"}, method="POST")
        try:
            with self._opener.open(request, timeout=cfg.timeout_s) as response:
                if response.status != 202: raise BridgeError("unexpected_http_status")
                return 202
        except urllib.error.HTTPError as exc:
            code = exc.code; exc.close()
            if code in {401, 403}: raise BridgeError(f"http_auth_{code}") from None
            if code not in {408, 410, 429} and code < 500:
                raise BridgeError(f"http_rejected_{code}") from None
            return code
        except (OSError, TimeoutError): return 0

    def poll_once(self, stop: threading.Event | None = None):
        cfg = self.config; results = []
        if not cfg.enabled: return results
        if self._state is None: raise BridgeError("bridge_closed")
        for mapping in cfg.mappings:
            if stop is not None and stop.is_set(): break
            quality, reason, value = "ok", "read_ok", None
            try:
                # Standard Modbus has no sample timestamp: conservative read-start time,
                # not HTTP-send time or a claim of sensor-internal acquisition time.
                observed_at = time.time()
                value = decode(mapping, read_registers(mapping, cfg.timeout_s))
            except (OSError, TimeoutError, BridgeError) as exc:
                quality, reason = "invalid", str(exc) if isinstance(exc, BridgeError) else "modbus_transport_error"
                observed_at = time.time()  # Observation of read failure, never a zero measurement.
            if stop is not None and stop.is_set(): break
            sequence = self._state.next(json.dumps([cfg.server.rstrip('/'), cfg.station, mapping.source, mapping.name]))
            packet = {"source": mapping.source, "name": mapping.name, "sequence": sequence,
                "observed_at": observed_at, "ttl_ms": cfg.ttl_ms, "quality": quality, "value": value}
            code = self._send(packet)
            results.append({"source": mapping.source, "name": mapping.name, "sequence": sequence,
                "quality": quality, "reason": reason, "http_status": code})
        return results

    def close(self):
        if self._state is not None: self._state.close(); self._state = None
        self._token = ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读 Modbus TCP → StepEye 认证信号桥")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--state", type=Path, help="持久序号文件；默认配置文件旁同名 .state.sqlite3")
    parser.add_argument("--once", action="store_true", help="只采集一轮，不后台驻留")
    args = parser.parse_args(argv); bridge = None
    try:
        if args.config.stat().st_size > 65536: raise BridgeError("config_too_large")
        config = BridgeConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
        if not config.enabled:
            print(json.dumps({"status": "disabled"})); return 0
        bridge = ModbusBridge(config, args.state or args.config.with_suffix(".state.sqlite3"))
        stop = threading.Event()
        for kind in (signal.SIGINT, signal.SIGTERM): signal.signal(kind, lambda *_: stop.set())
        while not stop.is_set():
            started = time.monotonic(); rows = bridge.poll_once(stop)
            print(json.dumps({"status": "polled", "items": rows}, ensure_ascii=False), flush=True)
            if args.once: return 0 if all(r["quality"] == "ok" and r["http_status"] == 202 for r in rows) else 1
            stop.wait(max(0., config.interval_s - (time.monotonic() - started)))
        return 0
    except (ValueError, OSError, sqlite3.Error, BridgeError) as exc:
        # Pydantic errors include input values; never print exceptions/config bodies.
        code = str(exc) if isinstance(exc, BridgeError) else "configuration_or_local_state_error"
        print(json.dumps({"status": "error", "code": code})); return 2
    finally:
        if bridge is not None: bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Real local Modbus TCP sockets -> real build_app auth/thermal signal endpoint."""
from contextlib import contextmanager
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn
import yaml

from tools.modbus_signal_bridge import (BridgeConfig, BridgeError, Mapping, ModbusBridge,
                                        SequenceStore, decode, main, read_registers)


def mapping(**changes):
    return Mapping.model_validate(dict(source="modbus-device", name="temperature", host="127.0.0.1",
        port=502, unit_id=7, function=4, address=0, datatype="int16", quantity="temperature",
        unit="C", scale=.1, min_value=-50., max_value=150.) | changes)


def config(port=502, server="http://127.0.0.1:8000", **changes):
    return BridgeConfig.model_validate(dict(enabled=True, server=server, station="sensor-station",
        timeout_s=.2, interval_s=.1, ttl_ms=2000, mappings=[mapping(port=port).model_dump()]) | changes)


def exact(connection, count):
    data = b""
    while len(data) < count:
        chunk = connection.recv(count-len(data))
        if not chunk: return data
        data += chunk
    return data


@contextmanager
def simulator(mode="ok", value=b"\x03\x20"):
    requests = []
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            raw = exact(self.request, 12)
            if len(raw) != 12: return
            tid, protocol, length, unit, function, address, count = struct.unpack(">HHHBBHH", raw)
            requests.append((protocol, length, unit, function, address, count))
            if mode == "timeout": time.sleep(.25); return
            if mode == "truncated": self.request.sendall(b"\x00"); return
            pdu = bytes([function, len(value)]) + value
            if mode == "exception": pdu = bytes([function | 128, 2])
            if mode == "bad_function": pdu = bytes([6, len(value)]) + value
            if mode == "bad_count": pdu = bytes([function, 99]) + value
            header = struct.pack(">HHHB", tid + (1 if mode == "bad_tid" else 0),
                1 if mode == "bad_protocol" else 0,
                65535 if mode == "bad_length" else len(pdu)+1,
                unit-1 if mode == "bad_unit" else unit)
            # Fragmentation verifies recv_exact, not recv(ADU) assumptions.
            try:
                self.request.sendall(header[:3]); self.request.sendall(header[3:]+pdu)
            except OSError: pass
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval":.02}, daemon=True)
    thread.start()
    try: yield server.server_address[1], requests
    finally: server.shutdown(); server.server_close(); thread.join(2)


@pytest.mark.parametrize("changes,raw,expected", [
    ({}, struct.pack(">h", -123), {"temperature":-12.3,"temperature_unit":"C"}),
    ({"datatype":"uint16", "offset":-5.}, struct.pack(">H", 850), {"temperature":80.,"temperature_unit":"C"}),
    ({"datatype":"float32", "scale":1.}, struct.pack(">f", 45.5), {"temperature":45.5,"temperature_unit":"C"}),
    ({"datatype":"float32", "word_order":"little", "scale":1.}, b"\x00\x00\x42\x36", {"temperature":45.5,"temperature_unit":"C"}),
    ({"datatype":"float32", "byte_order":"little", "scale":1.}, b"\x36\x42\x00\x00", {"temperature":45.5,"temperature_unit":"C"}),
    ({"datatype":"uint32", "quantity":"resistance", "unit":"Mohm", "scale":1e-6,
      "min_value":0., "max_value":1000.}, struct.pack(">I",10000000), {"quantity":"resistance","value":10.,"unit":"Mohm"}),
    ({"quantity":"gas_concentration","unit":"ppm","min_value":0.,"max_value":10000.},
     struct.pack(">h",500), {"quantity":"gas_concentration","value":50.,"unit":"ppm"}),
])
def test_fixed_units_scaling_signed_float_and_byte_word_order(changes, raw, expected):
    assert decode(mapping(**changes), raw) == expected


@pytest.mark.parametrize("changes,raw", [
    ({"datatype":"float32"}, struct.pack(">f",float("nan"))),
    ({"datatype":"float32"}, struct.pack(">f",float("inf"))),
    ({}, struct.pack(">h",2000)), ({"invalid_raw":[65535]}, b"\xff\xff"), ({}, b"\x00"),
])
def test_invalid_measurement_never_becomes_zero(changes, raw):
    with pytest.raises(BridgeError): decode(mapping(**changes),raw)


@pytest.mark.parametrize("changes", [
    {"function":6}, {"datatype":"float64"}, {"unit":"F"}, {"scale":0.}, {"scale":float("nan")},
    {"min_value":200.}, {"datatype":"uint32","address":65535}, {"host":"http://device"},
    {"invalid_raw":[65536]}, {"unit_id":0}, {"source":"contains space"}, {"unknown":"ignored?"},
])
def test_invalid_or_write_mappings_rejected(changes):
    with pytest.raises(ValueError): mapping(**changes)


@pytest.mark.parametrize("changes", [
    {"server":"http://192.0.2.3"}, {"server":"https://user:secret@example.com"},
    {"server":"https://example.com/other"}, {"token_env":"bad name"}, {"mappings":[]},
    {"ttl_ms":100}, {"mappings":[mapping().model_dump(),mapping().model_dump()]}, {"token":"secret"},
])
def test_config_strict_origin_ttl_and_no_inline_secret(changes):
    with pytest.raises(ValueError): config(**changes)


def test_socket_read_only_and_fragmented_response():
    for function in (3,4):
        with simulator() as (port, seen):
            assert read_registers(mapping(port=port,function=function,address=17),.2)==b"\x03\x20"
            assert seen==[(0,6,7,function,17,1)]


@pytest.mark.parametrize("mode", ["timeout","truncated","exception","bad_tid","bad_protocol","bad_length","bad_unit","bad_function","bad_count"])
def test_socket_protocol_faults_are_bounded(mode):
    with simulator(mode) as (port,_):
        start=time.monotonic()
        with pytest.raises((BridgeError,OSError)): read_registers(mapping(port=port),.08)
        assert time.monotonic()-start<.6


def test_durable_sequence_single_owner_and_reopen(tmp_path):
    path=tmp_path/"sequences.db";store=SequenceStore(path)
    try:
        assert store.next("same-key")==1
        with pytest.raises(BridgeError,match="state_already_in_use"): SequenceStore(path)
        code="from pathlib import Path;from tools.modbus_signal_bridge import SequenceStore;SequenceStore(Path(__import__('sys').argv[1]))"
        child=subprocess.run([sys.executable,"-c",code,str(path)],capture_output=True,timeout=10)
        assert child.returncode!=0 and b"state_already_in_use" in child.stderr
    finally: store.close(); store.close()
    reopened=SequenceStore(path)
    try: assert reopened.next("same-key")==2
    finally: reopened.close()


def test_disabled_and_stop_do_not_open_sockets_or_state(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(socket,"create_connection",lambda *a,**k:pytest.fail("disabled bridge connected"))
    path=tmp_path/"disabled.json";path.write_text('{"enabled":false}')
    assert main(["--config",str(path)])==0
    assert json.loads(capsys.readouterr().out)=={"status":"disabled"}
    assert not list(tmp_path.glob("*.sqlite3*"))
    bridge=ModbusBridge(BridgeConfig(),tmp_path/"unused.db")
    assert bridge.poll_once()==[];bridge.close()


@contextmanager
def real_api(tmp_path):
    from visual.auth import AuthStore
    from visual.server import build_app
    path=tmp_path/"station.yaml"
    path.write_text(yaml.safe_dump({"station":"sensor-station","source":{"type":"sim"},"hands":{"enabled":False},
        "signal_sources":["modbus-device"],"zones":[],"thermal":{"enabled":True,"rules":[{
        "id":"overheat","source":"modbus-device","name":"temperature","kind":"temperature",
        "threshold":60,"min_s":0.,"min_samples":2}]}}),encoding="utf8")
    auth=AuthStore(tmp_path/"auth.db");auth.set_user("bridge","test-only-password","operator")
    auth.set_user("viewer","test-only-password","viewer")
    app=build_app([path],tmp_path/"data",auth=auth)
    sock=socket.socket();sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    # Real HTTP/auth/router; capture deliberately not started, no camera is required.
    server=uvicorn.Server(uvicorn.Config(app,lifespan="off",log_level="critical",access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.02)
    assert server.started
    try: yield f"http://127.0.0.1:{port}",app.state.manager,auth
    finally:
        server.should_exit=True;thread.join(5);sock.close();app.state.manager.stop();auth.close()
        assert not thread.is_alive()


def test_real_modbus_to_authenticated_http_alarm_failure_clear_and_restart(tmp_path,monkeypatch):
    with real_api(tmp_path) as (url,manager,auth),simulator() as (port,seen):
        # Authentication via the actual network login endpoint; no handler substitute.
        req=urllib.request.Request(url+"/api/auth/login",data=json.dumps({"username":"bridge","password":"test-only-password"}).encode(),headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=2) as response:
            assert json.load(response)["ok"] is True
            cookie=SimpleCookie();cookie.load(response.headers["Set-Cookie"]);token=cookie["vsession"].value
        monkeypatch.setenv("STEPEYE_BRIDGE_TOKEN",token)
        bridge=ModbusBridge(config(port,url),tmp_path/"bridge.db")
        first=bridge.poll_once();time.sleep(.03);second=bridge.poll_once()
        assert first[0]["http_status"]==second[0]["http_status"]==202
        assert second[0]["sequence"]==2
        rt=manager.get("sensor-station")
        assert rt.frame_idx==0 and rt.raw is None
        assert manager.alerts._db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]==1
        assert rt.thermal_service.snapshot()["rules"]["overheat"]["status"]=="alert"
        bridge.close()
        with simulator("exception") as (bad_port,_):
            bridge=ModbusBridge(config(bad_port,url),tmp_path/"bridge.db")
            row=bridge.poll_once()[0];bridge.close()
        assert row==dict(source="modbus-device",name="temperature",sequence=3,quality="invalid",reason="modbus_exception_02",http_status=202)
        assert rt.thermal_service.snapshot()["rules"]["overheat"]["status"]=="unknown"
        audit=manager.signal_store.audit("sensor-station")
        assert any(a["sequence"]==3 and a["quality"]=="invalid" for a in audit)
        assert all(x[3] in {3,4} for x in seen)
        # Viewer and expired sessions fail closed, with no credential echo.
        for token,code in [(auth.login("viewer","test-only-password"),403),("invalid-session-token",401)]:
            monkeypatch.setenv("STEPEYE_BRIDGE_TOKEN",token)
            bridge=ModbusBridge(config(port,url),tmp_path/"bridge.db")
            try:
                with pytest.raises(BridgeError,match=f"http_auth_{code}"): bridge.poll_once()
            finally: bridge.close()


def test_stop_before_poll_and_no_token_leak(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv("STEPEYE_BRIDGE_TOKEN","local-token")
    bridge=ModbusBridge(config(),tmp_path/"bridge.db");stop=threading.Event();stop.set()
    try: assert bridge.poll_once(stop)==[]
    finally: bridge.close()
    path=tmp_path/"invalid.json";path.write_text('{"enabled":true,"token":"do-not-print-this-secret"}')
    assert main(["--config",str(path)])==2
    assert "do-not-print-this-secret" not in capsys.readouterr().out

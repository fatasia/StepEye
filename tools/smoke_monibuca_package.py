"""Verify a local native media binary/config without camera or external streams."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

import yaml


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def verify(binary, template):
    binary, template = Path(binary).resolve(), Path(template).resolve()
    help_result = subprocess.run([str(binary), '--help'], capture_output=True, timeout=10)
    version = subprocess.run([str(binary), '--version'], capture_output=True, timeout=10)
    if help_result.returncode or version.returncode:
        raise RuntimeError('Monibuca CLI check failed')
    config = yaml.safe_load(template.read_text(encoding='utf8'))
    port = free_port()
    config['global']['http']['listenaddr'] = f'127.0.0.1:{port}'
    # Only config/HTTP startup is tested; no camera, stream, or recorder is started.
    config['rtmp']['enable'] = False
    config['webrtc']['enable'] = False
    config['mp4']['enable'] = False
    with tempfile.TemporaryDirectory(prefix='stepeye-monibuca-smoke-') as folder:
        request = Path(folder) / 'config.yaml'
        request.write_text(yaml.safe_dump(config), encoding='utf8')
        process = subprocess.Popen([str(binary), '-c', str(request)], cwd=folder,
            env={**os.environ, 'M7S_ADMIN_DEV_MODE': '1'}, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline, status = time.monotonic() + 15, None
            while time.monotonic() < deadline and process.poll() is None:
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=.5)
                try:
                    connection.request('GET', '/')
                    response = connection.getresponse()
                    status = response.status
                    response.read(1024)
                    break
                except (OSError, http.client.HTTPException):
                    time.sleep(.1)
                finally:
                    connection.close()
            if status is None:
                raise RuntimeError('Monibuca config did not start an HTTP listener')
        finally:
            if process.poll() is None: process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
    return {'version': version.stdout.decode('utf8', errors='replace').strip(),
            'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'template_sha256': hashlib.sha256(template.read_bytes()).hexdigest(),
            'help_exit_code': help_result.returncode, 'config_http_status': status,
            'owned_process_exited': process.poll() is not None,
            'scope': 'CLI and config/HTTP startup only; no camera or WebRTC test'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.binary, args.config)
    with args.output.open('x', encoding='utf8') as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result))

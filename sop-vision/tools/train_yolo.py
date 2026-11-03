"""Launch a training worker in an explicitly selected isolated Python environment."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True, type=Path, help='Isolated environment Python executable')
    parser.add_argument('--spec', required=True, type=Path, help='Trusted local JSON; see docs/YOLO-TRAINING.md')
    args = parser.parse_args(argv)
    if not args.python.is_file() or not args.spec.is_file():
        parser.error('python and spec must be existing local files')
    worker = Path(__file__).resolve().parents[1] / 'visual' / 'training_worker.py'
    # No shell interpolation and no installation/download in the live runtime.
    process = subprocess.Popen([str(args.python.resolve()), str(worker), '--spec', str(args.spec.resolve())],
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.kill()
        process.wait()
        return 130


if __name__ == '__main__':
    raise SystemExit(main())

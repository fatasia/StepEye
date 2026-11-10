#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
exec "${PYTHON:-python3}" -m visual serve "${1:-configs/demo_sim.yaml}" --host 0.0.0.0 --port "${PORT:-8000}" --data data --auth

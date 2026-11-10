#!/bin/sh
set -e
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "[StepEye] 创建虚拟环境..."
  python3 -m venv .venv
fi
. .venv/bin/activate
echo "[StepEye] 安装依赖（首次较慢）..."
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt
echo "[StepEye] 启动演示工位: http://127.0.0.1:8000"
exec python -m visual serve configs/demo_sim.yaml --port 8000

"""单次固定表盘模型评估；不猜刻度，不下载模型，不覆盖报告。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from visual.gauge import GaugeCalibration, GaugeService
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--min-value', type=float)
    parser.add_argument('--max-value', type=float)
    parser.add_argument('--unit')
    parser.add_argument('--direction', choices=['clockwise', 'counterclockwise'])
    args = parser.parse_args()
    supplied = [args.min_value, args.max_value, args.unit, args.direction]
    if any(v is not None for v in supplied) and not all(v is not None for v in supplied):
        parser.error('物理读数需同时显式提供min-value、max-value、unit、direction')
    calibration = (None if args.min_value is None else GaugeCalibration(
        min_value=args.min_value, max_value=args.max_value, unit=args.unit, direction=args.direction))
    if args.output.exists():
        parser.error('输出已存在，不覆盖已有评估')
    with args.image.open('rb') as image:
        raw = image.read(20 * 1024 * 1024 + 1)
    service = GaugeService(ROOT)
    try:
        result = service.trial(raw, calibration)
    finally:
        service.close()
    result['image_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'status': result['status'], 'detections': len(result['detections']),
                      'reading': result['reading'], 'unit': result['unit']}, ensure_ascii=True))


if __name__ == '__main__':
    main()

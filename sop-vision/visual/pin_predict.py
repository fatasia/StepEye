"""Private one-image segmentation worker. Native runtime dies with this process."""
import json
from pathlib import Path
import sys


def predict(spec):
    from PIL import Image
    import onnxruntime as ort
    from .label_jobs import fingerprint
    from .pin_segmentation import segment
    model = Path(spec['model_path'])
    if fingerprint(model) != spec['model_sha256']:
        raise ValueError('model fingerprint mismatch')
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), options, providers=['CPUExecutionProvider'])
    with Image.open(spec['image_path']) as image:
        mask, stats = segment(image, session)
    Image.fromarray(mask * 255).save(spec['mask_path'], 'PNG')
    return stats


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError('request too large')
        print(json.dumps(predict(json.loads(raw)), allow_nan=False))
    except Exception:
        print(json.dumps({'error': 'pin_segmentation_failed'}))
        raise SystemExit(1)

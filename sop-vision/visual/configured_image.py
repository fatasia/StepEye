"""Bounded OpenAI-compatible text-to-image adapter for explicitly bound services."""
import base64
import io
import json
import time
import urllib.request
import uuid

from PIL import Image
from .ai_services import NoRedirect, validate_url

MAX_IMAGE = 10 * 1024 * 1024
MAX_RESPONSE = 15 * 1024 * 1024


def image_request(cfg, prompt):
    """Only inline images: never follow model-returned URLs or redirects."""
    base = validate_url(cfg.base_url)
    if base.endswith('/chat/completions'):
        base = base[:-len('/chat/completions')]
    headers = {'Content-Type':'application/json'}
    if cfg.api_key:
        headers['Authorization'] = 'Bearer ' + cfg.api_key
    req = urllib.request.Request(base + '/images/generations',
        data=json.dumps({'model':cfg.model, 'prompt':prompt, 'n':1,
                         'size':'1024x1024', 'response_format':'b64_json'}).encode(), headers=headers)
    deadline = time.monotonic() + 30
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=30) as response:
        raw = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('image request timed out')
            sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_RESPONSE:
                raise ValueError('image response too large')
    payload = json.loads(raw)
    value = payload['data'][0]['b64_json']
    if not isinstance(value, str) or len(value) > (MAX_IMAGE + 2) // 3 * 4:
        raise ValueError('inline image too large')
    return base64.b64decode(value, validate=True)


def normalize_image(data):
    if not data or len(data) > MAX_IMAGE:
        raise ValueError('image size limit')
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ('PNG','JPEG','WEBP') or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('unsupported generated image')
        if max(image.size) > 4096 or image.width * image.height > 4_000_000:
            raise ValueError('image pixel limit')
        out = io.BytesIO()
        image.convert('RGB').save(out, format='JPEG', quality=90)
        if out.tell() > MAX_IMAGE:
            raise ValueError('normalized image size limit')
        return out.getvalue()


def configured_batch(ref_path, label, description, n, out_dir, cfg, *, requester=image_request):
    if cfg is None:
        raise RuntimeError('尚未绑定图片生成服务，请在设置中配置')
    if not description.strip():
        raise ValueError('已配置服务需要填写生成描述')
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for _ in range(n):
        row = {'file':None, 'label':label, 'engine':'configured', 'source':ref_path.name,
               'synthetic':True, 'review_required':True, 'label_origin':'user_intent',
               'model':cfg.model, 'note':'文生图；参考图未发送，标签待人工复核，仅训练用途'}
        try:
            data = normalize_image(requester(cfg, description))
            name = 'configured-' + uuid.uuid4().hex + '.jpg'
            path = out_dir / name
            try:
                path.write_bytes(data)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            row['file'] = name
        except Exception:
            # Do not serialize remote error bodies, URLs, headers, or credentials.
            row['error'] = '图片生成失败：检查服务支持 b64_json、连接及图片大小/格式'
        rows.append(row)
    return rows

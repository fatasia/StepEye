"""On-demand sample generation in FastAPI's worker pool, never in the async camera loop."""
import io
import hashlib
import threading
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image
from .samplegen import COGVIEW_MAX_BATCH, generate_batch, write_manifest


def router(manager):
    api = APIRouter()
    slot = threading.Lock()

    @api.get('/api/samples/generation-status')
    def status():
        return {'configured':manager.ai_services.catalog()['bindings'].get('image') is not None,
                'protocol':'images/generations b64_json', 'reference_mode':'text_only'}

    @api.post('/api/samples/generate')
    def generate(ref: UploadFile = File(...), label: str = Form(...), engine: str = Form('augment'),
                 n: int = Form(10), description: str = Form('与参考图同类场景'), seed: int = Form(7)):
        if engine not in ('augment','cogview','lk888','configured'):
            raise HTTPException(400, '未知样本生成引擎')
        if not 1 <= n <= (100 if engine == 'augment' else COGVIEW_MAX_BATCH):
            raise HTTPException(400, '增强数量 1–100，生成式数量 1–8')
        if label.upper() not in ('OK','NG') or not 1 <= len(description) <= 4000:
            raise HTTPException(400, '标签须为 OK/NG，描述长度 1–4000')
        if not slot.acquire(False):
            raise HTTPException(429, '已有样本生成任务，请稍后重试')
        temp = None
        try:
            data = ref.file.read(10 * 1024 * 1024 + 1)
            if not data or len(data) > 10 * 1024 * 1024:
                raise HTTPException(413, '参考图为空或超过 10 MB')
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ('PNG','JPEG','WEBP') or getattr(image, 'n_frames', 1) != 1:
                    raise ValueError('参考图须为单帧 PNG/JPEG/WEBP')
                if max(image.size) > 4096 or image.width * image.height > 4_000_000:
                    raise ValueError('参考图最多400万像素、边长4096')
                image.verify()
            root = manager.data_dir / 'samplegen'
            root.mkdir(parents=True, exist_ok=True)
            source_sha256 = hashlib.sha256(data).hexdigest()
            references = root / 'references'
            references.mkdir(exist_ok=True)
            original = references / (source_sha256 + '.original')
            if not original.exists():
                original.write_bytes(data)
            temp = root / ('ref-' + uuid.uuid4().hex + '.jpg')
            temp.write_bytes(data)
            cfg = manager.ai_services.image_config() if engine == 'configured' else None
            rows = generate_batch(temp, label.upper(), description, n, root/'batch',
                                  engine=engine, seed=seed, cfg=cfg)
            for row in rows:
                row.update(source_sha256=source_sha256, source=original.relative_to(root).as_posix(),
                           group_id='sample-reference:' + source_sha256)
            mp = write_manifest(root/'batch', rows)
            count = sum(bool(row.get('file')) for row in rows)
            return {'ok':count == len(rows), 'generated':count, 'rows':rows,
                    'manifest':mp.relative_to(manager.data_dir).as_posix(),
                    'manifest_hint':'生成式标签是用户意图，待人工复核；仅训练用途，不进验收集'}
        except HTTPException:
            raise
        except (ValueError, OSError):
            raise HTTPException(400, '图片或参数无效，生成未完成') from None
        except Exception:
            raise HTTPException(502, '生成服务未就绪或调用失败，请检查绑定与配置') from None
        finally:
            try:
                if temp is not None:
                    temp.unlink(missing_ok=True)
            finally:
                slot.release()

    return api

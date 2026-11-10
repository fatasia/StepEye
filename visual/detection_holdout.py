"""Run one fixed detector against a frozen LabelStore validation split."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import zipfile

from .detection_eval import average_precision, evaluate_boxes
from .detector_registry import create_backend
from .coco_metrics import coco_bbox_metrics


def sha_file(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def _read(z, name, limit):
    item = z.getinfo(name)
    if item.file_size > limit or item.flag_bits & 1:
        raise ValueError('Archive member exceeds limits or is encrypted')
    return z.read(item)


def run_holdout(dataset, model, output, *, model_sha256, dataset_sha256,
                adapter='yolo_raw', confidence=.25, iou=.5):
    """CLI-only trusted local paths. Does not extract archive paths or publish models."""
    import cv2
    import numpy as np
    from PIL import Image
    import io
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    # Validate operating point before loading an inference session.
    evaluate_boxes(['check'], [{'id':'check','reviewed':True,'truth':[], 'predictions':[]}],
                   confidence_threshold=confidence, iou_threshold=iou)
    if adapter not in ('yolo_raw','yolo_e2e','kairos_rtdetr','glue_yolov4'):
        raise ValueError('Unsupported adapter')
    if Path(dataset).stat().st_size > 4 * 1024**3 or Path(model).stat().st_size > 1024**3:
        raise ValueError('Dataset/model exceeds local evaluation size limit')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='stepeye-eval-') as td:
        # Snapshot once: concurrent replacement cannot change the evaluated artifact.
        snapshot = Path(td)
        shutil.copyfile(model, snapshot/'model.onnx')
        shutil.copyfile(dataset, snapshot/'dataset.zip')
        if sha_file(snapshot/'model.onnx') != model_sha256 or sha_file(snapshot/'dataset.zip') != dataset_sha256:
            raise ValueError('Pinned SHA256 mismatch')
        with zipfile.ZipFile(snapshot/'dataset.zip') as z:
            names = z.namelist()
            if len(names)>100000 or len(names)!=len(set(names)):
                raise ValueError('Duplicate or excessive ZIP entries')
            raw = _read(z,'manifest.json',64*1024**2)
            manifest = json.loads(raw)
            meta = json.loads(_read(z,'metadata.json',2*1024**2))
            manifest_sha = hashlib.sha256(raw).hexdigest()
            if meta['manifest_sha256'] != manifest_sha or meta['dataset_version_id'] != 'LV-'+manifest_sha[:32]:
                raise ValueError('Version fingerprint mismatch')
            classes = manifest['project']['classes']
            assignments = manifest['assignments']
            assets = manifest['assets']
            ids = [a['id'] for a in assets]
            if len(set(ids)) != len(ids):
                raise ValueError('Duplicate asset IDs')
            # Check stated groups AND image/original hashes across exported splits.
            seen = {}
            for a in assets:
                split = assignments[a['id']]['split']
                for key in [('group',assignments[a['id']]['group']), ('source',a['group_id']),
                            ('image',a['image_sha256']), ('original',a['sha256'])]:
                    if not key[1]: continue
                    if key in seen and seen[key] != split:
                        raise ValueError('Cross-split source/hash overlap')
                    seen[key] = split
            synthetic_groups = {assignments[a['id']]['group'] for a in assets if a['synthetic']}
            selected, excluded = [], []
            for a in assets:
                if assignments[a['id']]['split'] != 'val': continue
                if assignments[a['id']]['group'] in synthetic_groups or a['state'] not in ('confirmed','background') or a['producer']!='human' or not a['actor']:
                    excluded.append({'id':a['id'],'reason':'synthetic_or_unconfirmed'});continue
                if not a['targets'] and (a['state']!='background' or f"labels/val/{a['id']}.txt" not in meta['reviewed_negatives']):
                    raise ValueError('Empty truth lacks explicit human-reviewed negative status')
                selected.append(a)
            if not 1 <= len(selected) <= 500:
                raise ValueError('Validation split must contain 1..500 eligible images; no implicit subsampling')
            # AP needs low-score detections, independent of the displayed operating point.
            score_floor = min(.001, confidence)
            backend = create_backend(snapshot/'model.onnx',classes,score_floor,max_results=100,adapter=adapter)
            frames, sources, dimensions = [], [], {}
            try:
                metadata = backend.session.get_modelmeta().custom_metadata_map
                if metadata.get('task','detect') != 'detect':
                    raise ValueError('Classification model cannot run detection evaluation')
                # Dedicated adapters validate their fixed class order.
                names = (list(backend.classes) if adapter in ('kairos_rtdetr', 'glue_yolov4') else
                         ast.literal_eval(metadata.get('names', 'None')))
                if isinstance(names, dict):
                    names = [names.get(i) for i in range(len(names))]
                if names != classes:
                    raise ValueError('Model metadata class order must match frozen project classes')
                for a in selected:
                    path = f"images/val/{a['id']}.png"
                    image = _read(z,path,20*1024**2)
                    if hashlib.sha256(image).hexdigest() != a['image_sha256']:
                        raise ValueError('Image SHA256 mismatch')
                    with Image.open(io.BytesIO(image)) as header:
                        if header.width*header.height>20000000 or header.size!=(a['width'],a['height']):
                            raise ValueError('Image dimensions mismatch/exceed limit')
                    frame = cv2.imdecode(np.frombuffer(image,np.uint8),cv2.IMREAD_COLOR)
                    if frame is None: raise ValueError('Invalid image')
                    h,w=frame.shape[:2]
                    dimensions[a['id']] = (w, h)
                    hits=backend.detect(frame)
                    predictions=[{'label':hit.label,'box':[hit.xyxy[0]/w,hit.xyxy[1]/h,hit.xyxy[2]/w,hit.xyxy[3]/h],
                                  'confidence':hit.confidence} for hit in hits]
                    truth=[]
                    for t in a['targets']:
                        cid=t['label_id']
                        if type(cid)!=int or not 0<=cid<len(classes): raise ValueError('Invalid truth class')
                        truth.append({'label':classes[cid],'box':t['xyxy_norm']})
                    frames.append({'id':a['id'],'reviewed':True,'truth':truth,'predictions':predictions})
                    sources.append({'id':a['id'],'archive_member':path,'revision':a['revision'],
                                    'image_sha256':a['image_sha256'],'source_group':a['group_id'],
                                    'split_group':assignments[a['id']]['group'],'state':a['state']})
            finally:
                backend.close()
    result=evaluate_boxes(classes,frames,iou_threshold=iou,confidence_threshold=confidence)
    # AP 在同一份导出 val 帧上计算,不是独立未见数据;边界声明随字段入库。
    report={'schema_version':1,'kind':'detection_validation','model_sha256':model_sha256,
            'dataset_sha256':dataset_sha256,'manifest_sha256':manifest_sha,
            'dataset_version_id':meta['dataset_version_id'],'adapter':adapter,'split':'val',
            'scope':'exported group/hash-isolated validation; physical workpiece independence not verified',
            'model_training_overlap':'unknown; validation split is not an independent unseen holdout claim',
            'publication':'not_requested','excluded':excluded,'sources':sources,'frames':frames,
            'evaluation':result,'average_precision':average_precision(classes,frames),
            'prediction_protocol': {'score_floor': score_floor, 'max_detections_per_image': 100},
            'coco_bbox': coco_bbox_metrics(classes, frames, dimensions),
            'elapsed_ms':round((time.monotonic()-started)*1000,2)}
    output.parent.mkdir(parents=True,exist_ok=True)
    # Same-filesystem hard link publishes complete bytes without overwriting an existing report.
    fd,temp=tempfile.mkstemp(prefix='.eval-',suffix='.json',dir=output.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(report,f,ensure_ascii=False,allow_nan=False,indent=2);f.flush();os.fsync(f.fileno())
        os.link(temp,output)
    finally:
        os.unlink(temp)
    return report

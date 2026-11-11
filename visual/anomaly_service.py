"""良品库、不可变版本与检测历史。所有结果入历史，仅 NG 生成告警。"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import uuid
import io

import cv2
import numpy as np

from .anomaly import AnomalyEngine, dinov2_path, normalize_exposure
from .recipes import _atomic_write


class AnomalyService:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.data_dir / 'anomaly'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'history.db', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, scene TEXT, body TEXT)')
        self.db.commit()
        self._job=None
        self._dispatch_lock=threading.Lock()
        self._last={}
        self._closed=False

    def close(self):
        with self._dispatch_lock:
            self._closed=True
            job=self._job
        if job:job.join(timeout=10)
        self.db.close()
        from .anomaly import _model_cache
        _model_cache.clear()

    def observe(self, rt):
        """相机外观检查每5秒取最新健康画面；繁忙时跳过，不堆积视频帧。"""
        if not self._dispatch_lock.acquire(blocking=False):return
        try:self._observe(rt)
        finally:self._dispatch_lock.release()

    def _observe(self, rt):
        if self._closed or self._job and self._job.is_alive():return
        sid=rt.cfg.station
        if sid not in self.manager.applications.appearance_ids:return
        try:row=self.manager.applications.get(sid)
        except ValueError:return
        if row['task']!='appearance' or time.monotonic()-self._last.get(sid,0)<5:return
        if rt.cfg.quality.enabled and rt.quality.snapshot().get('observation')!='ok':return
        if rt.raw is None or not (self.path(sid)/'engine.json').exists():return
        self._last[sid]=time.monotonic()
        frame=rt.raw.copy()
        def work():
            try:
                _,raw=cv2.imencode('.jpg',frame)
                self.inspect(sid,raw.tobytes(),guard=lambda:not self._closed and self.manager.runtimes.get(sid) is rt and not rt.stop_event.is_set())
            except (ValueError,RuntimeError):
                pass
        self._job=threading.Thread(target=work,name='appearance-latest',daemon=True)
        self._job.start()

    def path(self, scene):
        if not re.fullmatch(r'[\w-][\w.-]{0,79}', scene) or scene in ('.','..'):
            raise ValueError('场景名称请使用1–80位文字、字母、数字、横线或下划线')
        path = self.root / scene
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError('场景路径无效')
        return path

    @staticmethod
    def image(raw):
        if not raw or len(raw)>20*1024*1024:
            raise ValueError('单张图片最大20MB')
        try:
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.width*picture.height>4_000_000 or min(picture.size)<28 or getattr(picture,'n_frames',1)!=1:
                    raise ValueError('请选择至少28×28、最多400万像素的单帧图片')
        except (OSError,Image.DecompressionBombError):
            raise ValueError('图片无法读取') from None
        img = cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        if img is None or min(img.shape[:2])<28 or img.shape[0]*img.shape[1]>4_000_000:
            raise ValueError('请选择至少28×28、最多400万像素的有效图片')
        return img

    def list(self):
        with self.lock:
            return [{'scene':p.parent.name, **json.loads(p.read_text(encoding='utf8'))}
                    for p in self.root.glob('*/engine.json') if not p.parent.is_symlink()]

    def versions(self, scene):
        return [p.name for p in sorted((self.path(scene)/'versions').glob('*'),key=lambda p:p.stat().st_mtime_ns,reverse=True) if p.is_dir()]

    def snapshot(self, scene):
        path=self.path(scene)
        version=uuid.uuid4().hex
        dest=path/'versions'/version;dest.mkdir(parents=True)
        for name in ('bank.npz','engine.json','refs.json'):
            if (path/name).exists():shutil.copy2(path/name,dest/name)
        return version

    def register(self, scene, payloads, threshold, backend, lighting=None):
        if backend not in (None,'grid','dinov2') or lighting not in (None,'off','auto') or not .02<=threshold<=1:
            raise ValueError('请选择特征方式，阈值须为0.02–1')
        if not 1<=len(payloads)<=8:raise ValueError('每次登记1–8张良品')
        images=[self.image(p) for p in payloads]
        with self.lock:
            path=self.path(scene);meta=path/'engine.json'
            eng=AnomalyEngine.load(path) if meta.exists() else AnomalyEngine(threshold,backend or 'grid',lighting=lighting or 'off')
            if backend and backend!=eng.backend:raise ValueError('此版本已绑定其他特征方式，请复制为新场景后切换')
            if lighting and lighting!=eng.lighting:raise ValueError('请先保存光照处理设置，再登记良品')
            if eng.lighting == 'auto' and any(normalize_exposure(img)[1]['unusable'] for img in images):
                raise ValueError('参考图曝光严重不足或过曝，请调整拍摄后重试')
            refs=json.loads((path/'refs.json').read_text()) if (path/'refs.json').exists() else []
            if len(refs)+len(images)>64:raise ValueError('每个场景最多64张参考图，请创建新版本场景')
            if meta.exists():self.snapshot(scene)
            path.mkdir(parents=True,exist_ok=True)
            added=0
            for raw,img in zip(payloads,images):
                digest=hashlib.sha256(raw).hexdigest()
                if digest in refs:continue
                features=eng._features(img)
                if eng.n_refs+len(features)>20000:raise ValueError('特征库已满，请裁剪检查区域或创建新场景')
                eng.memory.add(features);eng.n_refs=len(eng.memory.bank)
                cv2.imwrite(str(path/(digest+'.jpg')),img)
                cv2.imwrite(str(path/('ref-'+digest+'.png')),img)
                refs.append(digest)
                added+=1
            eng.save(path)
            _atomic_write(path/'refs.json',json.dumps(refs).encode())
            return {'ok':True,'scene':scene,'total_refs':eng.n_refs,'added':added,'backend':eng.backend}

    def remove_reference(self, scene, ref):
        if not re.fullmatch('[a-f0-9]{64}',ref):raise ValueError('参考图片无效')
        with self.lock:
            path=self.path(scene);refs_path=path/'refs.json'
            refs=json.loads(refs_path.read_text()) if refs_path.exists() else []
            if ref not in refs:raise ValueError('参考图片不存在')
            if len(refs)<=1:raise ValueError('至少保留一张良品参考图')
            remaining=[r for r in refs if r!=ref]
            if any(not (path/('ref-'+r+'.png')).is_file() for r in remaining):
                raise ValueError('旧版本缺少原始参考图，请重新创建场景后登记')
            previous=AnomalyEngine.load(path)
            rebuilt=AnomalyEngine(previous.threshold,previous.backend,previous.model_sha,previous.lighting)
            for item in remaining:
                img=cv2.imread(str(path/('ref-'+item+'.png')),cv2.IMREAD_COLOR)
                if img is None:raise ValueError('参考图无法读取，未修改当前版本')
                rebuilt.add_ok(img)
            self.snapshot(scene)
            rebuilt.save(path)
            _atomic_write(refs_path,json.dumps(remaining).encode())
            return {'ok':True,'remaining':len(remaining)}

    def inspect(self, scene, raw, save=True, guard=None):
        img=self.image(raw)
        with self.lock:
            path=self.path(scene)
            if not (path/'engine.json').exists():raise ValueError('请先登记良品图片')
            eng=AnomalyEngine.load(path)
            result=eng.inspect(img)
            if not save:return result
            if guard and not guard():return result
            rid=uuid.uuid4().hex
            cv2.imwrite(str(path/(rid+'.jpg')),img)
            alert_id=None
            if result['verdict']=='NG' and self.manager.applications.publish_alerts(scene):
                record,new=self.manager.alerts.create(scene,'anomaly','NG','warn',f"外观异常分 {result['score']}",ref_id='appearance' if guard else rid)
                alert_id=record['id']
                if new:self.manager._push_out(record)
            if self.manager.shadow_store is not None:
                self.manager.shadow_store.add('anomaly',scene,rid,result['verdict'],result['score'],{'score':result['score']})
            value={'id':rid,'created_at':time.time(),'alert_id':alert_id,'scene':scene,**result}
            self.db.execute('INSERT INTO results VALUES (?,?,?)',(rid,scene,json.dumps(value)))
            self.db.commit()
            old=list(self.db.execute('SELECT id FROM results WHERE scene=? ORDER BY rowid DESC LIMIT -1 OFFSET 100',(scene,)))
            for (old_id,) in old:
                (path/(old_id+'.jpg')).unlink(missing_ok=True)
                self.db.execute('DELETE FROM results WHERE id=?',(old_id,))
            self.db.commit()
            return {'ok':True,**value}

    def history(self, scene):
        self.path(scene)
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM results WHERE scene=? ORDER BY rowid DESC LIMIT 30',(scene,))]

    def settings(self, scene, threshold, lighting=None):
        if not np.isfinite(threshold) or not .02<=threshold<=1:raise ValueError('阈值须为0.02–1')
        if lighting not in (None,'off','auto'):raise ValueError('请选择有效的光照处理方式')
        with self.lock:
            path=self.path(scene);eng=AnomalyEngine.load(path)
            if lighting is not None and lighting != eng.lighting:
                refs_path=path/'refs.json'
                refs=json.loads(refs_path.read_text()) if refs_path.exists() else []
                if not refs:raise ValueError('缺少原始参考图，请重新登记良品')
                rebuilt=AnomalyEngine(threshold,eng.backend,eng.model_sha,lighting)
                for ref in refs:
                    img=cv2.imread(str(path/('ref-'+ref+'.png')),cv2.IMREAD_COLOR)
                    if img is None:raise ValueError('缺少原始参考图，无法更换光照处理')
                    if lighting=='auto' and normalize_exposure(img)[1]['unusable']:
                        raise ValueError('参考图曝光严重不足或过曝，请调整拍摄后重试')
                    rebuilt.add_ok(img)
                eng=rebuilt
            self.snapshot(scene)
            eng.threshold=threshold;eng.save(path)
            return {'ok':True,'threshold':threshold,'lighting':eng.lighting}

    def restore(self, scene, version):
        if not re.fullmatch('[a-f0-9]{32}',version):raise ValueError('版本无效')
        with self.lock:
            path=self.path(scene);source=path/'versions'/version
            if not source.is_dir():raise ValueError('版本不存在')
            self.snapshot(scene)
            for name in ('bank.npz','engine.json','refs.json'):
                if (source/name).exists():_atomic_write(path/name,(source/name).read_bytes())
            return {'ok':True}

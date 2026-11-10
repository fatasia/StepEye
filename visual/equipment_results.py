"""设备图像/结果的原子导入；原判不可变，人工复判和既有inspection任务分开保存。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid

from .equipment_results_types import EquipmentResultBody, EquipmentReviewBody, EquipmentRecheckBody
from .label_jobs import fingerprint
from .labels import LabelError
from .storage_quota import quota_write
from .model_routes import ModelTrials

MAX_IMAGES, MAX_FILE, MAX_TOTAL = 5, 10*1024*1024, 25*1024*1024


def _signature(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


class EquipmentResults:
    def __init__(self,root: Path, quota=None):
        self.quota = quota
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.records=self.root/"records";self.staging=self.root/"staging"
        if any(p.is_symlink() for p in (self.records,self.staging,self.root/"equipment.db")):
            raise LabelError("设备存储目录不允许符号链接")
        self.records.mkdir(exist_ok=True);self.staging.mkdir(exist_ok=True)
        self._guard=threading.RLock();self._dispatch=threading.Lock();self._closed=False
        self._db=sqlite3.connect(str(self.root/"equipment.db"),check_same_thread=False,isolation_level=None,timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS equipment_results (id TEXT PRIMARY KEY, device TEXT, sn TEXT, verdict TEXT, observed REAL, idem TEXT, signature TEXT, body TEXT, UNIQUE(device,idem))")
        self._db.execute("CREATE INDEX IF NOT EXISTS equipment_lookup ON equipment_results(device,sn,observed)")
        self._db.execute("CREATE TABLE IF NOT EXISTS equipment_reviews (id TEXT PRIMARY KEY, result_id TEXT, idem TEXT, signature TEXT, body TEXT, UNIQUE(result_id,idem))")
        self._db.execute("CREATE TABLE IF NOT EXISTS equipment_rechecks (id TEXT PRIMARY KEY, result_id TEXT, idem TEXT, signature TEXT, body TEXT, UNIQUE(result_id,idem))")
        for table in ("equipment_results","equipment_reviews"):
            for operation in ("UPDATE","DELETE"):
                self._db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'original result and human reviews are immutable'); END")

    def _active(self):
        if self._closed: raise LabelError("设备结果存储已关闭",503)

    def _remove_created(self,path):
        path=path.resolve()
        if (path.parent not in {self.records.resolve(),self.staging.resolve()} or not path.name.startswith("ER-")):
            raise RuntimeError("拒绝清理非本任务目录")
        shutil.rmtree(path,ignore_errors=True)

    @quota_write(LabelError)
    def ingest(self,payload,files: list[tuple[str,bytes]],actor: str):
        body=payload if isinstance(payload,EquipmentResultBody) else EquipmentResultBody.model_validate(payload)
        if not 1<=len(files)<=MAX_IMAGES: raise LabelError("每次需要1–5张设备图片")
        if any(not isinstance(data,bytes) or not 1<=len(data)<=MAX_FILE for _,data in files) or sum(len(data) for _,data in files)>MAX_TOTAL:
            raise LabelError("图片每张最多10MB，总计最多25MB",413)
        inputs=body.model_dump(mode="json",exclude_none=True)
        signature=_signature({"metadata":inputs,"images":[hashlib.sha256(data).hexdigest() for _,data in files]})
        rid="ER-"+uuid.uuid4().hex;stage=self.staging/rid;final=self.records/rid;moved=False;created_stage=False
        with self._guard:
            self._active();self._db.execute("BEGIN IMMEDIATE")
            try:
                prior=self._db.execute("SELECT signature,body FROM equipment_results WHERE device=? AND idem=?",(body.device_id,body.idempotency_key)).fetchone()
                if prior:
                    if prior[0]!=signature: raise LabelError("设备幂等键已绑定不同结果或图片",409)
                    self._db.execute("COMMIT");return json.loads(prior[1]),False
                if self.quota is not None:
                    self.quota.check(self.root, sum(len(data) for _, data in files))
                stage.mkdir(exist_ok=False);created_stage=True;images=[];pixels=0
                for i,(name,data) in enumerate(files):
                    original=stage/f"{i}.original";image=stage/f"{i}.png"
                    original.write_bytes(data)
                    width,height=ModelTrials._image(data,image);pixels+=width*height
                    if pixels>40000000: raise LabelError("整批图片总像素最多4000万",413)
                    images.append({"index":i,"name":Path(name.replace("\\","/")).name[:200],"width":width,"height":height,
                                   "original_sha256":hashlib.sha256(data).hexdigest(),"image_sha256":fingerprint(image)})
                if self.quota is not None:
                    self.quota.check(self.root)  # Include decoded PNGs, not just compressed originals.
                if final.exists(): raise LabelError("结果目录冲突",409)
                stage.replace(final);moved=True
                record={**inputs,"id":rid,"actor":actor,"received_at":time.time(),"images":images,
                        "image_urls":[f"/api/equipment-results/{rid}/images/{i}" for i in range(len(images))],
                        "origin":"equipment","human_truth":False,"signature":signature}
                self._db.execute("INSERT INTO equipment_results VALUES (?,?,?,?,?,?,?,?)",(rid,body.device_id,body.sn,body.result,body.observed_at.timestamp(),body.idempotency_key,signature,json.dumps(record,ensure_ascii=False)))
                self._db.execute("COMMIT");return record,True
            except Exception:
                if self._db.in_transaction:self._db.execute("ROLLBACK")
                if created_stage and stage.exists():self._remove_created(stage)
                if moved:self._remove_created(final)
                raise

    def get(self,rid):
        with self._guard:
            self._active();row=self._db.execute("SELECT body FROM equipment_results WHERE id=?",(rid,)).fetchone()
        if not row: raise LabelError("设备结果不存在",404)
        return json.loads(row[0])

    def list(self,device_id="",sn="",result="",limit=50,offset=0):
        if result not in {"","OK","NG","UNCERTAIN"} or type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=1000000:
            raise LabelError("筛选或分页参数无效")
        where="WHERE (?='' OR device=?) AND (?='' OR sn=?) AND (?='' OR verdict=?)"
        args=(device_id,device_id,sn,sn,result,result)
        with self._guard:
            self._active();total=self._db.execute("SELECT COUNT(*) FROM equipment_results "+where,args).fetchone()[0]
            rows=self._db.execute("SELECT body FROM equipment_results "+where+" ORDER BY observed DESC,id LIMIT ? OFFSET ?",args+(limit,offset)).fetchall()
        return {"total":total,"items":[json.loads(r[0]) for r in rows]}

    def inspection_faces(self,product_id,sn,inspection_id):
        """Exact product/SN/run binding; never merge reinspection with an old run."""
        with self._guard:
            self._active()
            rows=self._db.execute("SELECT body FROM equipment_results WHERE sn=? "
                "AND json_extract(body,'$.product_id')=? AND json_extract(body,'$.inspection_id')=? "
                "ORDER BY observed,id LIMIT 101",(sn,product_id,inspection_id)).fetchall()
        if len(rows)>100:raise LabelError('同次六面检查超过100条记录，请核对检测批次',409)
        return [json.loads(row[0]) for row in rows]

    def image_path(self,rid,index):
        record=self.get(rid)
        if type(index) is not int or not 0<=index<len(record["images"]):raise LabelError("设备图片不存在",404)
        path=self.records/record["id"]/f"{index}.png"
        if (not path.resolve().is_relative_to(self.records) or path.is_symlink() or not path.is_file()
                or fingerprint(path)!=record["images"][index]["image_sha256"]):
            raise LabelError("设备图片完整性校验失败",409)
        return path

    def review(self,rid,payload,actor):
        body=payload if isinstance(payload,EquipmentReviewBody) else EquipmentReviewBody.model_validate(payload)
        self.get(rid);value=body.model_dump();signature=_signature(value)
        record={**value,"id":"ERH-"+uuid.uuid4().hex,"result_id":rid,"actor":actor,"created_at":time.time(),"producer":"human"}
        with self._guard:
            self._active();self._db.execute("BEGIN IMMEDIATE")
            try:
                prior=self._db.execute("SELECT signature,body FROM equipment_reviews WHERE result_id=? AND idem=?",(rid,body.idempotency_key)).fetchone()
                if prior:
                    if prior[0]!=signature:raise LabelError("复判幂等键冲突",409)
                    record=json.loads(prior[1])
                else:self._db.execute("INSERT INTO equipment_reviews VALUES (?,?,?,?,?)",(record["id"],rid,body.idempotency_key,signature,json.dumps(record,ensure_ascii=False)))
                self._db.execute("COMMIT");return record
            except Exception:
                if self._db.in_transaction:self._db.execute("ROLLBACK")
                raise

    def recheck(self,rid,payload,actor,inspection):
        body=payload if isinstance(payload,EquipmentRecheckBody) else EquipmentRecheckBody.model_validate(payload)
        self.get(rid)
        if inspection is None:raise LabelError("拍照即检未配置，设备原判已保留",503)
        signature=_signature(body.model_dump());key=(rid,body.idempotency_key)
        if not self._dispatch.acquire(False):raise LabelError("已有复判提交正在进行，请稍后重试",429)
        try:
            with self._guard:
                self._active();self._db.execute("BEGIN IMMEDIATE")
                try:
                    prior=self._db.execute("SELECT signature,body FROM equipment_rechecks WHERE result_id=? AND idem=?",key).fetchone()
                    if prior:
                        if prior[0]!=signature:raise LabelError("机器复判幂等键冲突",409)
                        self._db.execute("COMMIT");return json.loads(prior[1])
                    scenario=inspection.scenarios.get(body.scenario_id)
                    if scenario is None:raise LabelError("复判场景不存在",404)
                    if not scenario.enabled:raise LabelError("复判场景未启用",409)
                    if inspection._q.qsize()>=16:raise LabelError("即检队列繁忙，请稍后重试",429)
                    record={"id":"ERM-"+uuid.uuid4().hex,"result_id":rid,"scenario_id":body.scenario_id,
                            "idempotency_key":body.idempotency_key,"actor":actor,"producer":"machine_suggestion",
                            "created_at":time.time(),"status":"dispatch_unknown","task_id":None}
                    self._db.execute("INSERT INTO equipment_rechecks VALUES (?,?,?,?,?)",(record["id"],*key,signature,json.dumps(record,ensure_ascii=False)))
                    self._db.execute("COMMIT")
                except Exception:
                    if self._db.in_transaction:self._db.execute("ROLLBACK")
                    raise
            try:
                raw=[self.image_path(rid,i).read_bytes() for i in range(len(self.get(rid)["images"]))]
                task=inspection.submit(body.scenario_id,raw)
                record.update(status="submitted",task_id=task["id"])
            except Exception:
                record.update(status="failed",error="复判提交失败；设备原判未改变")
            with self._guard:
                self._active();self._db.execute("UPDATE equipment_rechecks SET body=? WHERE id=?",(json.dumps(record,ensure_ascii=False),record["id"]))
            return record
        finally:self._dispatch.release()

    def reviews(self,rid,inspection=None):
        self.get(rid)
        with self._guard:
            rows=self._db.execute("SELECT body FROM equipment_reviews WHERE result_id=? UNION ALL SELECT body FROM equipment_rechecks WHERE result_id=?",(rid,rid)).fetchall()
        records=[json.loads(r[0]) for r in rows]
        for record in records:
            if record.get("task_id") and inspection is not None:
                task=inspection.store.get(record["task_id"])
                if task:record["inspection"]={k:task.get(k) for k in ("id","status","verdict","confidence","reason")}
        return {"items":sorted(records,key=lambda r:r["created_at"],reverse=True)}

    def close(self):
        with self._guard:
            if not self._closed:self._closed=True;self._db.close()

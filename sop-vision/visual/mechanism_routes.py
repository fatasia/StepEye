"""机理报告 API；沿用质量报告的不可变 SQLite 模式，不修改质量分析种类。"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from fastapi import APIRouter,HTTPException,Request,Response
from .mechanism import MechanismBody,analyze


class MechanismReports:
    def __init__(self,db_path):
        path=Path(db_path);path.parent.mkdir(parents=True,exist_ok=True)
        self._lock=threading.Lock();self._slots=threading.BoundedSemaphore(1)
        self._db=sqlite3.connect(str(path),check_same_thread=False,timeout=10)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('CREATE TABLE IF NOT EXISTS mechanism_reports(id TEXT PRIMARY KEY, created REAL, body TEXT)')
        for op in ('UPDATE','DELETE'):
            self._db.execute(f"CREATE TRIGGER IF NOT EXISTS mechanism_no_{op.lower()} BEFORE {op} ON mechanism_reports BEGIN SELECT RAISE(ABORT,'reports are immutable'); END")
        self._db.commit();self._closed=False

    def create(self,body,actor='local'):
        body=body if isinstance(body,MechanismBody) else MechanismBody.model_validate(body)
        if not self._slots.acquire(blocking=False):raise RuntimeError('机理分析忙，请稍后重试')
        try:
            inputs=body.model_dump()
            canonical=json.dumps(inputs,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf8')
            result=analyze(body)
            report={'id':'ML-'+uuid.uuid4().hex,'schema_version':1,'kind':'cycle_health_trend',
                    'title':body.title,'unit':body.unit,'actor':actor,'created_at':time.time(),
                    'input_sha256':hashlib.sha256(canonical).hexdigest(),'inputs':inputs,'result':result}
            raw=json.dumps(report,ensure_ascii=False,allow_nan=False)
            with self._lock:
                if self._closed:raise ValueError('机理报告服务已关闭')
                self._db.execute('INSERT INTO mechanism_reports VALUES(?,?,?)',(report['id'],report['created_at'],raw))
                self._db.commit()
            return report
        finally:self._slots.release()

    def get(self,report_id):
        with self._lock:
            row=self._db.execute('SELECT body FROM mechanism_reports WHERE id=?',(report_id,)).fetchone()
        if row is None:raise KeyError('机理报告不存在')
        return json.loads(row[0])

    def list(self,limit=50,offset=0):
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=1000000:
            raise ValueError('分页范围无效')
        with self._lock:
            total=self._db.execute('SELECT COUNT(*) FROM mechanism_reports').fetchone()[0]
            rows=self._db.execute('SELECT body FROM mechanism_reports ORDER BY created DESC,id LIMIT ? OFFSET ?',(limit,offset)).fetchall()
        items=[]
        for row in rows:
            report=json.loads(row[0]);items.append({k:v for k,v in report.items() if k not in ('inputs','result')}|{'status':report['result']['status']})
        return {'total':total,'items':items}

    def close(self):
        with self._lock:
            if not self._closed:self._closed=True;self._db.close()


def router(store:MechanismReports):
    api=APIRouter()

    @api.get('/api/mechanism/capabilities')
    def capabilities():
        return {'methods':['cycle_health_trend'],'csv_fields':['cycle','value'],'max_rows':20000,
                'max_cycles':2000,'min_cycles':24,'min_samples_per_cycle':4,'csv_max_bytes':2*1024*1024,
                'license':'MIT','source':'weihu/phm_core','production_approval':False,
                'supports_rul':False,'supports_fault_root_cause':False}

    @api.post('/api/mechanism/analyses',status_code=201)
    def create(body:MechanismBody,request:Request):
        try:return store.create(body,(getattr(request.state,'user',None) or {}).get('username','local'))
        except RuntimeError as exc:raise HTTPException(429,str(exc)) from None
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @api.get('/api/mechanism/analyses')
    def listing(limit:int=50,offset:int=0):
        try:return store.list(limit,offset)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    def lookup(report_id):
        try:return store.get(report_id)
        except KeyError:raise HTTPException(404,'机理报告不存在') from None

    @api.get('/api/mechanism/analyses/{report_id}/export')
    def export(report_id:str):
        report=lookup(report_id)
        return Response(json.dumps(report,ensure_ascii=False,allow_nan=False),media_type='application/json',
                        headers={'Content-Disposition':f'attachment; filename="{report["id"]}.json"'})

    @api.get('/api/mechanism/analyses/{report_id}')
    def detail(report_id:str):return lookup(report_id)

    return api

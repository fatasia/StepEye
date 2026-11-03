"""每相机一个按需头部探针 worker：latest pending=1，结果只消费一次。"""
from __future__ import annotations
from dataclasses import dataclass
import math
import threading
import time
import numpy as np
from .helmet_probe import HelmetROIProbe
from .tracking import iou


@dataclass(frozen=True)
class HelmetJob:
    generation: int
    epoch: int
    ts: float
    captured_mono: float
    frame: np.ndarray
    people: tuple


class HelmetShadow:
    """submit/drain 不等待推理；worker 空闲即释放 session 并退出，无常驻线程。"""
    def __init__(self,cfg,base_dir=None,*,ttl_s=.5,idle_s=10,factory=HelmetROIProbe,clock=time.monotonic):
        if not .05<=ttl_s<=5 or not .05<=idle_s<=300:raise ValueError('invalid helmet worker limits')
        self.cfg,self.base_dir,self.ttl_s,self.idle_s=cfg,base_dir,ttl_s,idle_s
        self.factory,self.clock=factory,clock
        self._cv=threading.Condition();self._pending=None;self._result=None;self._thread=None
        self._generation=0;self._epoch=None;self._closed=False;self._last_submit=None
        self.dropped=0;self.close_error=False

    def submit(self,frame,ts,tracks,epoch,*,captured_mono=None):
        if not self.cfg.enabled:return False
        now=self.clock();captured=now if captured_mono is None else captured_mono
        if not math.isfinite(ts) or not math.isfinite(captured) or type(epoch) is not int:raise ValueError('invalid frame identity')
        if now-captured<0 or now-captured>self.ttl_s:return False
        if (not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8 or frame.ndim!=3 or frame.shape[2]!=3
                or not frame.size or frame.shape[0]*frame.shape[1]>4_000_000):raise ValueError('invalid/big helmet frame')
        people=tuple(t for t in tracks if t.label=='person' and t.last_seen==ts)[:64]
        if not people:return False
        with self._cv:
            if self._closed:return False
            if epoch!=self._epoch or (self._last_submit is not None and ts<self._last_submit):
                self._generation+=1;self._epoch=epoch;self._pending=None;self._result=None;self._last_submit=None
            if self._last_submit is not None and ts-self._last_submit<self.cfg.interval_s:return False
            self._last_submit=ts
            job=HelmetJob(self._generation,epoch,ts,captured,frame.copy(),people)
            if self._pending is not None:self.dropped+=1
            self._pending=job
            if self._thread is None:
                self._thread=threading.Thread(target=self._run,name='stepeye-helmet',daemon=True)
                self._thread.start()
            self._cv.notify_all()
            return True

    def _run(self):
        probe=None;probe_generation=None
        try:
            while True:
                with self._cv:
                    ready=self._cv.wait_for(lambda:self._pending is not None or self._closed,timeout=self.idle_s)
                    if not ready or self._closed:return
                    job=self._pending;self._pending=None
                if self.clock()-job.captured_mono>self.ttl_s:continue
                try:
                    if probe is None:probe=self.factory(self.cfg,self.base_dir)
                    if probe_generation!=job.generation:
                        probe.reset_observations();probe_generation=job.generation
                    result=probe.process(job.frame,job.ts,job.people)
                except Exception:
                    result={'status':'unavailable','observations':[],'sampled_person_ids':[],'frame_ts':job.ts}
                with self._cv:
                    if (not self._closed and job.generation==self._generation and
                            self.clock()-job.captured_mono<=self.ttl_s and result.get('frame_ts')==job.ts):
                        self._result=(job,result)
        finally:
            try:
                if probe is not None:probe.close()
            except Exception:
                self.close_error=True
            with self._cv:
                self._thread=None
                # submit may race with idle exit; restart only if a bounded latest job is waiting.
                if self._pending is not None and not self._closed:
                    self._thread=threading.Thread(target=self._run,name='stepeye-helmet',daemon=True)
                    self._thread.start()
                self._cv.notify_all()

    def drain(self,epoch,ts,current_tracks):
        """返回原采样人框与证据；调用方不得写进当前帧 Context 或改写 last_seen。"""
        with self._cv:
            item=self._result;self._result=None
            generation=self._generation
        if item is None:return None
        job,result=item
        if (epoch!=job.epoch or generation!=job.generation or not math.isfinite(ts)
                or not 0<=ts-job.ts<=self.ttl_s or self.clock()-job.captured_mono>self.ttl_s):return None
        current={p.track_id:p for p in current_tracks if p.label=='person' and p.last_seen==ts}
        parents={p.track_id:p for p in job.people if p.track_id in current and iou(p.xyxy,current[p.track_id].xyxy)>=.2}
        sampled=[pid for pid in result.get('sampled_person_ids',[]) if pid in parents]
        if not sampled and result.get('status')!='sampled':sampled=list(parents)
        if not sampled:return None
        observations=[t for t in result.get('observations',[]) if t.last_seen==job.ts and t.parent_track_id in sampled]
        return {'epoch':job.epoch,'frame_ts':job.ts,'status':result['status'],
                'sampled_person_ids':sampled,'person_tracks':[parents[pid] for pid in sampled],
                'observations':observations,'captured_mono':job.captured_mono,'age_s':self.clock()-job.captured_mono}

    def invalidate(self):
        with self._cv:
            self._generation+=1;self._pending=None;self._result=None;self._last_submit=None
            self._cv.notify_all()

    def close(self,timeout_s=.5):
        with self._cv:
            self._closed=True;self._generation+=1;self._pending=None;self._result=None
            thread=self._thread;self._cv.notify_all()
        if thread is not None:thread.join(timeout=max(0,min(timeout_s,5)))
        return (thread is None or not thread.is_alive()) and not self.close_error


class HelmetSampleMonitor:
    """独立逐人采样计时器；不受其他人轮转或未采样视频帧影响。"""
    def __init__(self,ppe_cfg,geoms,*,ttl_s=.5,clock=time.monotonic):
        if not .05<=ttl_s<=5:raise ValueError('invalid evidence TTL')
        self.cfg,self.geoms,self.ttl_s,self.clock=ppe_cfg,geoms,ttl_s,clock
        self.reset()

    def reset(self):
        self._epoch=None;self._people={}

    def update(self,result,epoch,ts,current_tracks):
        from .ppe import PPEMonitor
        if epoch!=self._epoch:self.reset();self._epoch=epoch
        current={p.track_id:p for p in current_tracks if p.label=='person' and p.last_seen==ts}
        self._people={pid:value for pid,value in self._people.items()
                      if pid in current and 0<=ts-value[0]<=self.ttl_s
                      and 0<=self.clock()-value[2]<=self.ttl_s}
        if result is None:return []
        if result.get('status')!='sampled':
            self._people.clear()
            return []
        if (result.get('epoch')!=epoch
                or not 0<=ts-result['frame_ts']<=self.ttl_s
                or not 0<=self.clock()-result.get('captured_mono',-math.inf)<=self.ttl_s):return []
        alerts=[];sample_ts=result['frame_ts']
        for person in result['person_tracks'][:64]:
            pid=person.track_id
            if pid not in current or iou(person.xyxy,current[pid].xyxy)<.2:continue
            previous=self._people.get(pid)
            if previous is not None and sample_ts<=previous[0]:continue
            monitor=previous[1] if previous else PPEMonitor(self.cfg,self.geoms)
            evidence=[v for v in result['observations'] if v.parent_track_id==pid and v.last_seen==sample_ts]
            alerts+=monitor.process([person]+evidence,sample_ts)
            if pid not in self._people and len(self._people)>=64:
                oldest=min(self._people,key=lambda key:self._people[key][0]);self._people.pop(oldest)
            self._people[pid]=(sample_ts,monitor,result['captured_mono'])
        return alerts

    def snapshot(self):
        return {'epoch':self._epoch,'sampled_persons':[
            {'track_id':pid,'sample_ts':ts,'state':monitor.snapshot()}
            for pid,(ts,monitor,captured) in self._people.items()
            if 0<=self.clock()-captured<=self.ttl_s]}

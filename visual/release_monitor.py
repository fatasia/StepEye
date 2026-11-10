"""Request/event-driven release availability guard; no scheduler or accuracy inference."""
from __future__ import annotations
import json,math,sqlite3,threading,time
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field
from .model_release import validate_release_model

class MonitorPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    enabled:bool=False
    window_s:float=Field(default=30,ge=5,le=3600)
    min_samples:int=Field(default=5,ge=2,le=1000)
    max_frame_age_s:float=Field(default=10,ge=1,le=300)
    max_error_delta:int=Field(default=3,ge=1,le=10000)

class ArmBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    release_id:str=Field(min_length=1,max_length=100)
    policy:MonitorPolicy=Field(default_factory=MonitorPolicy)

class ReleaseMonitor:
    def __init__(self,path,releases,evaluations,*,clock=time.time):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path),check_same_thread=False,timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS release_monitors(scene TEXT PRIMARY KEY,body TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS release_monitor_events(id INTEGER PRIMARY KEY,at REAL,scene TEXT,body TEXT)')
        self.db.commit();self.lock=threading.Lock();self.releases=releases;self.evaluations=evaluations;self.clock=clock
        # Restart discards the observation window; committed intent is never replayed.
        for (raw,) in self.db.execute('SELECT body FROM release_monitors').fetchall():
            state=json.loads(raw)
            if state['status']=='monitoring':
                state.update(samples=[],epoch=None,last_sample_at=None,armed_at=self.clock())
                self._save(state)
            elif state['status']=='rollback_intent':
                active=self.releases.active_for(state['scene'])
                state['status']='pending_runtime_reload' if active and active['id']==state.get('target_release_id') else 'blocked'
                state['reason']='restart_after_rollback_intent'
                self._save(state)

    def _get(self,scene):
        row=self.db.execute('SELECT body FROM release_monitors WHERE scene=?',(scene,)).fetchone()
        return json.loads(row[0]) if row else None
    def _save(self,state,event=None):
        self.db.execute('INSERT INTO release_monitors VALUES(?,?) ON CONFLICT(scene) DO UPDATE SET body=excluded.body',
                        (state['scene'],json.dumps(state,allow_nan=False)))
        if event:self.db.execute('INSERT INTO release_monitor_events(at,scene,body) VALUES(?,?,?)',
                                (self.clock(),state['scene'],json.dumps(event,allow_nan=False)))
        self.db.commit()
    def get(self,scene):
        with self.lock:return self._get(scene)
    def active_scenes(self):
        with self.lock:
            return [scene for scene,raw in self.db.execute('SELECT scene,body FROM release_monitors ORDER BY scene')
                    if json.loads(raw)['status'] in ('monitoring','pending_runtime_reload')]
    def arm(self,scene,release_id,policy=None,*,actor):
        policy=MonitorPolicy.model_validate(policy or {})
        active=self.releases.active_for(scene)
        if not active or active['id']!=release_id:raise ValueError('Monitor must bind the current active release')
        if not isinstance(actor,str) or not actor.strip() or len(actor)>100:raise ValueError('Admin actor required')
        with self.lock:
            previous=self._get(scene)
            if previous and previous['release_id']==release_id and previous['status'] not in ('disabled','monitoring'):
                raise ValueError('Terminal monitor cannot be rearmed for the same release')
            state={'scene':scene,'release_id':release_id,'policy':policy.model_dump(),'armed_by':actor,
                   'armed_at':self.clock(),'status':'monitoring' if policy.enabled else 'disabled',
                   'epoch':None,'samples':[],'last_sample_at':None,'reason':None,'runtime_applied':False}
            self._save(state,{'action':'arm','actor':actor,'enabled':policy.enabled});return state
    def _verified_previous(self,current):
        target=self.releases.get(current.get('replaced_release_id') or '')
        if (target['status']!='superseded' or target['scene']!=current['scene']
                or not target.get('approved_by') or not target.get('activated_at')):
            raise ValueError('Previous release lacks verified approval/activation history')
        report=self.evaluations.get(target['evaluation_id'])
        if (report.get('kind')!='detection_validation' or report.get('publication')!='not_requested'
                or any(report.get(k)!=target[k] for k in ('engine_id','model_sha256','adapter'))
                or list((report.get('evaluation') or {}).get('classes') or {})!=target['classes']):
            raise ValueError('Previous release evaluation mismatch')
        validate_release_model(target)
        return target
    def record(self,scene,*,runtime_release_id,epoch,running,error_total,last_frame_at):
        """Only trusted server metrics. Never expose this method as a public sample endpoint."""
        now=self.clock()
        if (not isinstance(epoch,str) or not epoch or len(epoch)>100 or type(running)!=bool
                or type(error_total)!=int or error_total<0 or
                (last_frame_at is not None and (type(last_frame_at) not in (int,float)
                 or not math.isfinite(last_frame_at) or last_frame_at>now))):raise ValueError('Invalid trusted runtime sample')
        with self.lock:
            state=self._get(scene)
            if not state or state['status']!='monitoring':return state
            current=self.releases.active_for(scene)
            if not current or current['id']!=state['release_id']:
                state.update(status='superseded',reason='active_release_changed');self._save(state);return state
            if runtime_release_id!=state['release_id']:
                return state # awaiting actual runtime application; never sample the old model
            if state['last_sample_at'] is not None and now-state['last_sample_at']<1:return state
            if state['epoch']!=epoch or (state['samples'] and error_total<state['samples'][-1]['errors']):
                state.update(epoch=epoch,samples=[],armed_at=now)
            state['last_sample_at']=now
            age=now-(last_frame_at if last_frame_at is not None else state['armed_at'])
            state['samples'].append({'at':now,'errors':error_total,'bad_frame':not running or age>state['policy']['max_frame_age_s']})
            policy=state['policy'];samples=state['samples']
            # Keep one sample at/before window start for a genuine full elapsed window.
            while len(samples)>2 and samples[1]['at']<=now-policy['window_s']:samples.pop(0)
            if len(samples)<policy['min_samples'] or now-samples[0]['at']<policy['window_s']:
                self._save(state);return state
            errors=error_total-samples[0]['errors']
            reason='runtime_errors' if errors>=policy['max_error_delta'] else 'frame_availability' if all(s['bad_frame'] for s in samples) else None
            if not reason:self._save(state);return state
            try:target=self._verified_previous(current)
            except (ValueError,KeyError,OSError):
                state.update(status='blocked',reason='previous_release_unverified');self._save(state,{'action':'blocked','trigger':reason});return state
            # Durable intent before ledger write: a crash must not repeat an automatic action.
            state.update(status='rollback_intent',reason=reason,target_release_id=target['id'])
            self._save(state,{'action':'rollback_intent','trigger':reason,'target':target['id']})
            try:self.releases.rollback(current['id'],'release-monitor')
            except (ValueError,KeyError):
                state.update(status='rollback_failed');self._save(state,{'action':'rollback_failed'});return state
            state.update(status='pending_runtime_reload')
            self._save(state,{'action':'ledger_rolled_back','target':target['id']});return state
    def runtime_applied(self,scene,release_id):
        """Mainline invokes only after a successful reload of exactly the restored release."""
        with self.lock:
            state=self._get(scene);active=self.releases.active_for(scene)
            if not state or state['status']!='pending_runtime_reload' or state.get('target_release_id')!=release_id or not active or active['id']!=release_id:
                raise ValueError('Reload acknowledgement does not match restored release')
            state.update(status='rolled_back',runtime_applied=True);self._save(state,{'action':'runtime_applied'});return state
    def close(self):
        with self.lock:self.db.close()

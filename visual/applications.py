"""业务应用绑定工位、样本项目与输入源；配置仍以工位 YAML 为准。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import yaml
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .dsl import StationConfig, Program
from .recipes import _atomic_write


class ApplicationBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    task: Literal['sop', 'safety', 'appearance']
    engine_id: str = ''
    rule: Literal['intrusion', 'fire_smoke', 'aisle_occupied', 'ppe_helmet'] = 'intrusion'
    backend: Literal['grid', 'dinov2'] = 'grid'
    exposure_correction: Literal['off', 'auto'] = 'off'


class InputBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['sim', 'rtsp', 'camera'] = 'sim'
    uri: str = Field(default='', max_length=2000)


class Applications:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.data_dir / 'applications'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'apps.db', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS apps (id TEXT PRIMARY KEY, body TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, app TEXT, body TEXT)')
        self.db.commit()
        self.appearance_ids={r['id'] for r in (json.loads(row[0]) for row in self.db.execute('SELECT body FROM apps')) if r['task']=='appearance'}

    def close(self):
        self.db.close()

    def get(self, aid):
        with self.lock:
            row = self.db.execute('SELECT body FROM apps WHERE id=?', (aid,)).fetchone()
        if not row:
            raise ValueError('应用不存在')
        return json.loads(row[0])

    def save(self, row):
        with self.lock:
            if row['task']=='appearance':self.appearance_ids.add(row['id'])
            self.db.execute('INSERT OR REPLACE INTO apps VALUES (?,?)', (row['id'], json.dumps(row, ensure_ascii=False)))
            self.db.commit()
        return self.public(row)

    def public(self, row):
        # 相机地址可能包含密码，只公开类型与是否已经验证。
        result = {k: v for k, v in row.items() if k not in ('source', 'probe')}
        rt = self.manager.runtimes.get(row['id'])
        result['running'] = rt is not None and not rt.stop_event.is_set()
        result['input_type'] = row.get('source', {}).get('type')
        result['input_verified'] = bool(row.get('probe'))
        return result

    def list(self, include_archived=False):
        with self.lock:
            rows = [self.public(json.loads(r[0])) for r in self.db.execute('SELECT body FROM apps ORDER BY rowid DESC')]
        known = {r['id'] for r in rows}
        for sid, rt in list(self.manager.runtimes.items()):
            if sid not in known:
                rows.append({'id': sid, 'name': rt.cfg.name or sid, 'task': 'existing', 'running': True,
                             'state': 'running', 'legacy': True})
        return [r for r in rows if include_archived or r['state']!='archived']

    def create(self, body):
        row = {'id': 'APP-' + uuid.uuid4().hex[:12], **body.model_dump(), 'state': 'draft', 'project_id': ''}
        if not body.name.strip():
            raise ValueError('请输入应用名称')
        row['name'] = body.name.strip()
        if body.task == 'safety':
            model = self.manager.label_engines.snapshot(body.engine_id)
            if model.get('adapter') not in ('yolo_raw', 'yolo_e2e', 'kairos_rtdetr', 'glue_yolov4'):
                raise ValueError('请选择支持实时检测的模型')
            labels = model['classes']
            required = ['fire', 'smoke'] if body.rule == 'fire_smoke' else ['person']
            if not set(required) & set(labels):
                raise ValueError('模型类别与所选检查任务不兼容')
            if body.rule=='ppe_helmet':
                from .ppe import ALIASES
                helmet={ALIASES[c.lower()] for c in labels if c.lower() in ALIASES and ALIASES[c.lower()][0]=='helmet'}
                if not {('helmet',True),('helmet',False)}.issubset(helmet):
                    raise ValueError('安全帽检查需要同时识别人员、佩戴和未佩戴的模型')
        if body.engine_id:
            model = self.manager.label_engines.snapshot(body.engine_id)
            row['engine_sha']=model['model_sha256']
            project = self.manager.labels.create_project(row['name'], model['classes'], row['id'])
            row['project_id'] = project['id']
        return self.save(row)

    def probe(self, aid, source):
        row = self.get(aid)
        if row['state'] in ('trial', 'running','archived'):
            raise ValueError('先暂停应用再更换输入源')
        if source['type'] == 'rtsp' and not source.get('uri', '').lower().startswith(('rtsp://', 'rtsps://')):
            raise ValueError('请输入 rtsp:// 或 rtsps:// 相机地址')
        if source['type'] == 'camera' and source.get('uri', '') not in [str(i) for i in range(16)]:
            raise ValueError('USB 相机编号须为 0–15')
        attempts=2 if source['type']=='rtsp' else 1
        for attempt in range(attempts):
            try:
                process = subprocess.run([sys.executable, '-m', 'visual.source_probe'],
                    input=json.dumps(source), text=True, capture_output=True, timeout=15,
                    cwd=Path(__file__).resolve().parents[1],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                result = json.loads(process.stdout)
                if not process.returncode and 'error' not in result:break
                if process.returncode:logging.getLogger(__name__).warning('画面探测进程退出：%s',process.returncode)
                error=result.get('error','无法读取画面')
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                error='读取画面超时，请检查地址、账号、设备占用和网络连接'
            if attempt+1<attempts:time.sleep(.5)
        else:
            raise ValueError(error) from None
        row.update(source=source, probe={'width': result['width'], 'height': result['height']}, state='ready')
        path=self.path(aid)
        if path.exists():
            raw=yaml.safe_load(path.read_text(encoding='utf8'))
            raw['source']={**source,**row['probe'],'fps':25,'loop':True}
            _atomic_write(path,yaml.safe_dump(raw,allow_unicode=True).encode())
        self.save(row)
        return result

    def path(self, aid):
        return self.root / (self.get(aid)['id'] + '.yaml')

    def set_exposure(self, aid, mode):
        with self.lock:
            row=self.get(aid)
            if row['task']=='appearance':raise ValueError('外观应用请在良品检测中设置光照处理')
            if row['state'] in ('trial','running','archived'):
                raise ValueError('请先暂停应用，再更换曝光设置')
            path=self.path(aid)
            if path.exists():
                raw=yaml.safe_load(path.read_text(encoding='utf8'))
                raw['exposure_correction']=mode
                _atomic_write(path,yaml.safe_dump(raw,allow_unicode=True).encode())
            row['exposure_correction']=mode
            return self.save(row)

    def config(self, row):
        if self.path(row['id']).exists():
            return yaml.safe_load(self.path(row['id']).read_text(encoding='utf8'))
        source = {**row['source'], **row['probe'], 'fps': 25, 'loop': True}
        if source['type'] == 'camera':
            source['uri'] = int(source['uri'])
        raw = {'station': row['id'], 'name': row['name'], 'source': source,
               'exposure_correction':row.get('exposure_correction','off'),
               'hands': {'enabled': row['task'] == 'sop'}, 'quality': {'enabled': source['type'] != 'sim'},
               'appearance_enabled': row['task'] == 'appearance',
               'zones': [{'id': 'work_area', 'polygon': [[.05,.05],[.95,.05],[.95,.95],[.05,.95]]}]}
        if row['task'] == 'sop':
            raw.update(roi_states=[{'zone':'work_area','mode':'diff_empty'}],
                cycle={'start':{'obj_state':{'zone':'work_area','state':'present','min_ms':500}},
                       'end':{'obj_state':{'zone':'work_area','state':'absent','min_ms':500}}},
                steps=[{'id':'S1','name':'操作确认','evidence':{'all':[{'hand_dwell':{'zone':'work_area','min_ms':300}}]}}])
        if row['task'] == 'safety':
            m = self.manager.label_engines.snapshot(row['engine_id'])
            labels = [c for c in m['classes'] if c in (['fire','smoke'] if row['rule']=='fire_smoke' else ['person'])]
            selected=m['classes']
            if row['rule']=='ppe_helmet':
                from .ppe import ALIASES
                selected=[c for c in m['classes'] if c.lower()=='person' or (c.lower() in ALIASES and ALIASES[c.lower()][0]=='helmet')]
            raw['detector'] = {'enabled':True,'model':m['model_path'],'adapter':m.get('adapter','yolo_raw'),
                               'classes':selected,'class_names':m['classes']}
            if row['rule']=='ppe_helmet':raw['ppe']={'enabled':True,'zones':['work_area']}
            else:raw['ehs'] = {'enabled':True,'rules':[{'id':'check','kind':row['rule'],'zone':'work_area','labels':labels}]}
        return raw

    def start(self, aid, trial=True):
        with self.lock:
            row = self.get(aid)
            if row['state']=='archived':raise ValueError('请先恢复归档应用')
            if not row.get('probe'):
                raise ValueError('请先测试画面连接')
            if row['task'] == 'appearance' and not trial and not (self.manager.data_dir/'anomaly'/aid/'engine.json').exists():
                raise ValueError('先登记良品并核对检测效果')
            if row['task']=='safety':
                release=self.manager.model_releases.active_for(aid)
                if release:
                    from .label_jobs import fingerprint
                    path=Path(release.get('model_path') or '')
                    if not path.is_file() or fingerprint(path)!=release['model_sha256']:
                        raise ValueError('已发布模型缺失或版本不匹配，请恢复原模型或回滚')
                else:
                    model=self.manager.label_engines.snapshot(row['engine_id'])
                    if row.get('engine_sha') and model['model_sha256']!=row['engine_sha']:
                        raise ValueError('模型已变化，请通过应用的评测与发布流程更新版本')
            if row['task']=='appearance' and (self.manager.data_dir/'anomaly'/aid/'engine.json').exists():
                from .anomaly import AnomalyEngine,dinov2_fingerprint
                engine=AnomalyEngine.load(self.manager.data_dir/'anomaly'/aid)
                if engine.backend=='dinov2' and dinov2_fingerprint()!=engine.model_sha:
                    raise ValueError('DINOv2 模型与参考库版本不一致，请恢复匹配模型')
            wvp=row.get('source',{}).get('wvp')
            if wvp:
                from .gb28181 import WvpClient
                from .setup_routes import wvp_options
                cfg=wvp_options(self.manager)
                try:
                    uri=WvpClient(cfg['url'],cfg['username'],cfg['password']).play_url(wvp['device_id'],wvp['channel_id']).get('rtsp')
                except Exception:
                    raise ValueError('国标平台点播失败，请检查平台、设备和通道') from None
                if not isinstance(uri,str) or not uri.lower().startswith(('rtsp://','rtsps://')):
                    raise ValueError('国标平台未返回 RTSP 地址')
                row['source']['uri']=uri
            raw = self.config(row)
            if wvp:raw['source']['uri']=row['source']['uri']
            if row['task']=='sop' and raw['source']['type']!='sim' and not (Path(__file__).resolve().parents[1]/'models/hand_landmarker.task').exists():
                raise ValueError('尚未准备手部识别模型，请在环境检查中准备组件后重试')
            Program.from_config(StationConfig.model_validate(raw))
            _atomic_write(self.path(aid), yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode())
            previous=row['state']
            row['state'] = 'trial' if trial else 'running'
            # forwarder 在每次发告警前读取此状态，试运行仅留证，不通知。
            self.save(row)
            try:
                if aid not in self.manager.runtimes:
                    self.manager.add_station(self.path(aid))
            except Exception:
                row['state']=previous;self.save(row)
                raise
            return self.public(row)

    def pause(self, aid):
        row = self.get(aid)
        rt = self.manager.runtimes.get(aid)
        if rt:
            rt.stop()
            thread = self.manager._threads.get(aid)
            if thread:
                thread.join(timeout=8)
                if thread.is_alive():raise ValueError('输入源仍在关闭，请稍后重试')
            rt.signal_inbox.close()
            del self.manager.runtimes[aid]
            self.manager.paths.pop(aid, None)
        row['state'] = 'paused'
        return self.save(row)

    def publish_alerts(self, sid):
        try:
            return self.get(sid)['state'] == 'running'
        except ValueError:
            return True  # 旧工位沿用原有告警行为。

    def archive(self, aid):
        self.pause(aid)
        row=self.get(aid);row['state']='archived'
        return self.save(row)

    def record(self, sid, kind, payload):
        with self.lock:
            self.db.execute('INSERT INTO events(app,body) VALUES (?,?)', (sid, json.dumps({'kind':kind, **payload})))
            self.db.execute('DELETE FROM events WHERE app=? AND id NOT IN (SELECT id FROM events WHERE app=? ORDER BY id DESC LIMIT 100)', (sid,sid))
            self.db.commit()

    def events(self, sid):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM events WHERE app=? ORDER BY id DESC LIMIT 100', (sid,))]

    def restore(self):
        for row in self.list():
            if not row.get('legacy') and row['state'] in ('trial', 'running'):
                try:
                    self.start(row['id'], row['state']=='trial')
                except Exception:
                    data = self.get(row['id']); data['state'] = 'paused'; self.save(data)

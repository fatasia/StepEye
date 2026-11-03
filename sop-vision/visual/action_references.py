"""Immutable SOP reference cards extracted from saved timeline intervals, not action inference."""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

import cv2

from .timeline import TimelineStore


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class ActionReferenceStore:
    def __init__(self, root: Path, timeline_root: Path, timeline_db: Path):
        self.root, self.timeline_root, self.timeline_db = Path(root), Path(timeline_root), Path(timeline_db)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.root/'references.db'), check_same_thread=False, timeout=10)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('CREATE TABLE IF NOT EXISTS action_references (id TEXT PRIMARY KEY, station TEXT, step TEXT, version INTEGER, timeline TEXT, body TEXT, UNIQUE(station,step,version))')
        self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    def list(self, *, station_id=None, timeline_id=None):
        with self._lock:
            rows = self._db.execute('SELECT body FROM action_references WHERE (? IS NULL OR station=?) AND (? IS NULL OR timeline=?) ORDER BY rowid DESC LIMIT 500', (station_id,station_id,timeline_id,timeline_id)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, rid):
        with self._lock:
            row = self._db.execute('SELECT body FROM action_references WHERE id=?',(rid,)).fetchone()
        if not row:
            raise KeyError('动作参考包不存在')
        return json.loads(row[0])

    def frame(self, rid, role):
        record = self.get(rid)
        if role not in ('start','key','end'):
            raise ValueError('参考帧角色无效')
        path = self.root / record['id'] / (role+'.png')
        if not path.is_file() or path.is_symlink() or sha256(path) != record['frames'][role]['sha256']:
            raise ValueError('参考帧缺失或内容校验失败')
        return path

    def create(self, tid, interval_index, station_id, step_id, step_name, key_time_s, rules_hash, actor):
        timeline = TimelineStore(self.timeline_db)
        try:
            source = timeline.get(tid)
        finally:
            timeline.close()
        if source is None:
            raise KeyError('时间轴不存在')
        intervals = source['intervals']
        if not 0 <= interval_index < len(intervals):
            raise ValueError('请先保存有效时间轴区间')
        interval = intervals[interval_index]
        start, end = float(interval['start']), float(interval['end'])
        if not all(math.isfinite(x) for x in (start,end,key_time_s)) or not 0 <= start < key_time_s < end <= source['duration_s']:
            raise ValueError('关键时间必须位于已保存区间内部')
        video = (self.timeline_root/source['video']).resolve()
        if self.timeline_root.resolve() not in video.parents or video.is_symlink() or not video.is_file():
            raise ValueError('原视频不存在或路径非法')
        if video.stat().st_size > 1024*1024*1024:
            raise ValueError('参考视频超过1GB')
        video_hash = sha256(video)
        rid = uuid.uuid4().hex
        folder = self.root/rid
        folder.mkdir()
        capture = None
        try:
            capture = cv2.VideoCapture(str(video))
            if not capture.isOpened():
                raise ValueError('原视频无法解码')
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            width, height = capture.get(cv2.CAP_PROP_FRAME_WIDTH), capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
            if not (math.isfinite(fps) and fps > 0 and math.isfinite(count) and count >= 1
                    and 0 < width*height <= 16000000):
                raise ValueError('视频帧率、帧数或尺寸不可用')
            frames = {}
            for role, requested in [('start',start),('key',key_time_s),('end',end)]:
                # An interval ending at EOF uses the last decodable frame, never fabricates it.
                seek = min(requested, (count-1)/fps)
                capture.set(cv2.CAP_PROP_POS_MSEC, seek*1000)
                ok, frame = capture.read()
                if not ok:
                    raise ValueError(f'{role} 参考帧无法解码')
                timestamp = capture.get(cv2.CAP_PROP_POS_MSEC)/1000
                frame_index = capture.get(cv2.CAP_PROP_POS_FRAMES)-1
                path = folder/(role+'.png')
                if not cv2.imwrite(str(path), frame):
                    raise ValueError('参考帧写入失败')
                frames[role] = {'requested_time_s':requested, 'seek_time_s':seek,
                    'decoder_timestamp_s':timestamp if math.isfinite(timestamp) and timestamp >= 0 else None,
                    'decoder_frame_index':int(frame_index) if math.isfinite(frame_index) and frame_index >= 0 else None,
                    'width':int(frame.shape[1]),'height':int(frame.shape[0]),'sha256':sha256(path)}
            if sha256(video) != video_hash:
                raise ValueError('抽帧期间视频已变化，请重试')
            record = {'id':rid, 'timeline_id':tid, 'timeline_name':source['name'], 'interval_index':interval_index,
                'interval':{'start':start,'end':end,'step':interval.get('step','')}, 'station_id':station_id,
                'step_id':step_id,'step_name':step_name,'rules_hash':rules_hash,'video_sha256':video_hash,
                'video':source['video'],'frames':frames,'actor':actor,'created':datetime.now(timezone.utc).isoformat(),
                'purpose':'reference_only','applied_to_production':False}
            with self._lock:
                try:
                    self._db.execute('BEGIN IMMEDIATE')
                    version = self._db.execute('SELECT COALESCE(MAX(version),0)+1 FROM action_references WHERE station=? AND step=?',(station_id,step_id)).fetchone()[0]
                    record['version'] = version
                    manifest = json.dumps(record,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')
                    (folder/'manifest.json').write_bytes(manifest)
                    self._db.execute('INSERT INTO action_references VALUES(?,?,?,?,?,?)',(rid,station_id,step_id,version,tid,manifest.decode('utf-8')))
                    self._db.commit()
                except Exception:
                    self._db.rollback()
                    raise
            return record
        except Exception:
            # Only known files in this operation's newly allocated directory are removed.
            for name in ('start.png','key.png','end.png','manifest.json'):
                (folder/name).unlink(missing_ok=True)
            folder.rmdir()
            raise
        finally:
            if capture is not None:
                capture.release()

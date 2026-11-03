"""盘点账实核对:期望库存导入(版本化)与单机位可见计数对账(不可变报告)。

范围声明(S14 软件侧边界):对账输入是各 zone 当前可见计数快照(来自现有计数能力,
如 EHS object_count / 检测计数),因此这是"单机位可见计数对账,跨机位去重未实现";
跨机位去重需要跟踪/ReID 与多机位数据支撑,本模块不做,报告 scope 字段如实声明,
差异结论不等于物理在库数量。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 报告与差异表的固定范围声明:前端/下游不得将其解读为跨机位去重后的真实库存。
SCOPE = '单机位可见计数对账,跨机位去重未实现'


class ExpectedItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    zone: str = Field(min_length=1, max_length=64)
    item: str = Field(min_length=1, max_length=64)
    count: int = Field(ge=0, le=1_000_000, strict=True)


class ExpectedImportBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    station: str = Field(min_length=1, max_length=64)
    note: str = Field(default='', max_length=200)
    # 整批快照导入:每次导入替换该 station 全部期望值并产生新版本,历史留痕。
    items: list[ExpectedItem] = Field(min_length=1, max_length=1024)

    @model_validator(mode='after')
    def _unique_zone_item(self):
        keys = [(it.zone, it.item) for it in self.items]
        if len(set(keys)) != len(keys):
            raise ValueError('同一 station 内 zone+item 重复')
        return self


class ObservedCount(BaseModel):
    model_config = ConfigDict(extra='forbid')
    zone: str = Field(min_length=1, max_length=64)
    item: str = Field(min_length=1, max_length=64)
    count: int = Field(ge=0, le=1_000_000, strict=True)


class ReconcileBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    station: str = Field(min_length=1, max_length=64)
    note: str = Field(default='', max_length=200)
    observed: list[ObservedCount] = Field(default_factory=list, max_length=1024)


class StockReconStore:
    """期望库存(可更新、留版本历史)+ 对账报告(不可变)。"""

    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('CREATE TABLE IF NOT EXISTS expected_current (station TEXT PRIMARY KEY, body TEXT)')
        self._db.execute('CREATE TABLE IF NOT EXISTS expected_versions '
                         '(id TEXT PRIMARY KEY, station TEXT, version INTEGER, body TEXT, UNIQUE(station,version))')
        self._db.execute('CREATE TABLE IF NOT EXISTS recon_reports (id TEXT PRIMARY KEY, station TEXT, created REAL, body TEXT)')
        for operation in ('UPDATE', 'DELETE'):
            self._db.execute(f'CREATE TRIGGER IF NOT EXISTS recon_no_{operation.lower()} BEFORE {operation} ON recon_reports '
                             "BEGIN SELECT RAISE(ABORT,'reconciliation reports are immutable'); END")
        self._db.commit()
        self._closed = False

    def close(self):
        with self._guard:
            if not self._closed:
                self._closed = True
                self._db.close()

    # ---- 期望库存 ----
    def import_expected(self, body: ExpectedImportBody | dict, actor: str) -> dict:
        body = body if isinstance(body, ExpectedImportBody) else ExpectedImportBody.model_validate(body)
        with self._guard:
            self._check_open()
            try:
                self._db.execute('BEGIN IMMEDIATE')
                version = self._db.execute('SELECT COALESCE(MAX(version),0)+1 FROM expected_versions WHERE station=?',
                                           (body.station,)).fetchone()[0]
                record = {'id': uuid.uuid4().hex, 'station': body.station, 'version': version,
                          'items': [it.model_dump() for it in body.items],
                          'note': body.note, 'actor': actor, 'created': time.time()}
                encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
                self._db.execute('INSERT INTO expected_versions VALUES (?,?,?,?)',
                                 (record['id'], body.station, version, encoded))
                self._db.execute('INSERT INTO expected_current VALUES (?,?) '
                                 'ON CONFLICT(station) DO UPDATE SET body=excluded.body',
                                 (body.station, encoded))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return record

    def get_expected(self, station: str) -> dict:
        with self._guard:
            self._check_open()
            row = self._db.execute('SELECT body FROM expected_current WHERE station=?', (station,)).fetchone()
        if row is None:
            raise KeyError(f'站点 {station} 无期望库存')
        return json.loads(row[0])

    def list_expected(self) -> list[dict]:
        with self._guard:
            self._check_open()
            return [json.loads(r[0]) for r in self._db.execute('SELECT body FROM expected_current ORDER BY station')]

    def versions(self, station: str) -> list[dict]:
        self.get_expected(station)  # 未导入过的 station 一律 404 语义,不返回空列表
        with self._guard:
            self._check_open()
            return [json.loads(r[0]) for r in
                    self._db.execute('SELECT body FROM expected_versions WHERE station=? ORDER BY version DESC', (station,))]

    # ---- 对账 ----
    def reconcile(self, body: ReconcileBody | dict, actor: str) -> dict:
        body = body if isinstance(body, ReconcileBody) else ReconcileBody.model_validate(body)
        expected = self.get_expected(body.station)
        observed: dict[tuple[str, str], int] = {}
        for it in body.observed:
            key = (it.zone, it.item)
            if key in observed:
                raise ValueError(f'观测计数 zone+item 重复:{key[0]}/{key[1]}')
            observed[key] = it.count
        rows, summary = [], {'match': 0, 'missing': 0, 'surplus': 0, 'unobserved': 0, 'unexpected': 0}
        for item in expected['items']:
            key = (item['zone'], item['item'])
            if key not in observed:
                summary['unobserved'] += 1
                rows.append({**item, 'observed_count': None, 'status': 'unobserved'})
                continue
            diff = observed.pop(key) - item['count']
            status = 'match' if diff == 0 else ('missing' if diff < 0 else 'surplus')
            summary[status] += 1
            rows.append({**item, 'observed_count': item['count'] + diff, 'status': status})
        for zone, item in sorted(observed):  # 账外可见:期望值中没有的 zone+item
            summary['unexpected'] += 1
            rows.append({'zone': zone, 'item': item, 'count': None,
                         'observed_count': observed[(zone, item)], 'status': 'unexpected'})
        report = {'id': 'SR-' + uuid.uuid4().hex, 'schema_version': 1,
                  'station': body.station, 'created_at': time.time(), 'actor': actor,
                  'note': body.note, 'expected_version': expected['version'],
                  'scope': SCOPE, 'rows': rows, 'summary': summary}
        with self._guard:
            self._check_open()
            self._db.execute('INSERT INTO recon_reports VALUES (?,?,?,?)',
                             (report['id'], body.station, report['created_at'],
                              json.dumps(report, ensure_ascii=False, allow_nan=False)))
            self._db.commit()
        return report

    def get_report(self, report_id: str) -> dict:
        with self._guard:
            self._check_open()
            row = self._db.execute('SELECT body FROM recon_reports WHERE id=?', (report_id,)).fetchone()
        if row is None:
            raise KeyError('对账报告不存在')
        return json.loads(row[0])

    def list_reports(self, station: str = '', limit: int = 50, offset: int = 0) -> dict:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._guard:
            self._check_open()
            total = self._db.execute("SELECT COUNT(*) FROM recon_reports WHERE (?='' OR station=?)",
                                     (station, station)).fetchone()[0]
            rows = self._db.execute("SELECT body FROM recon_reports WHERE (?='' OR station=?) "
                                    'ORDER BY created DESC,id LIMIT ? OFFSET ?',
                                    (station, station, limit, offset)).fetchall()
        return {'total': total, 'reports': [json.loads(r[0]) for r in rows]}

    def _check_open(self):
        if self._closed:
            raise ValueError('盘点对账服务已关闭')

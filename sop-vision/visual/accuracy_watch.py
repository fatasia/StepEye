"""Report-driven quality watches on the existing SQLite connection; no polling."""
import hashlib
import json
import time

from pydantic import BaseModel, ConfigDict, Field
from .accuracy_monitor import compare_reports


class AccuracyWatchBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    min_samples: int = Field(default=20, ge=1, le=10000, strict=True)
    min_drop: float = Field(default=.1, gt=0, le=1, allow_inf_nan=False)


def scope_key(report):
    """Never mix projects, artifacts, class order or prediction protocols."""
    ev = report.get('evaluation') or {}
    if not report.get('project_id') or not report.get('engine_id'):
        return None
    values = [report['project_id'], report['engine_id'], report.get('model_sha256'),
              report.get('adapter'), list(ev.get('classes') or {}),
              ev.get('confidence_threshold'), ev.get('iou_threshold'), report.get('prediction_protocol')]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class AccuracyWatch:
    """Caller owns the report store lock/transaction for all methods."""
    def __init__(self, db):
        self.db = db
        db.execute('CREATE TABLE IF NOT EXISTS accuracy_watches(scope TEXT PRIMARY KEY,body TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS accuracy_observations(report_id TEXT PRIMARY KEY,body TEXT)')
        for op in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS accuracy_observations_no_{op.lower()} "
                       f"BEFORE {op} ON accuracy_observations BEGIN SELECT RAISE(ABORT,'immutable observation'); END")

    def _report(self, identifier):
        row = self.db.execute('SELECT body FROM holdout_evaluations WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise KeyError('评测报告不存在')
        return json.loads(row[0])

    def config(self, report):
        row = self.db.execute('SELECT body FROM accuracy_watches WHERE scope=?', (scope_key(report),)).fetchone()
        return json.loads(row[0]) if row else None

    def observation(self, identifier):
        row = self.db.execute('SELECT body FROM accuracy_observations WHERE report_id=?', (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def configure(self, identifier, body, actor):
        report = self._report(identifier)
        scope = scope_key(report)
        if not scope:
            raise ValueError('报告缺少项目或登记模型信息')
        body = AccuracyWatchBody.model_validate(body)
        # Validate the source contract using the existing comparison implementation.
        compare_reports(report, {**report, 'id': 'validation-copy'},
                        min_samples=body.min_samples, min_drop=body.min_drop)
        prior = self.config(report)
        state = {'baseline_id': identifier if body.enabled or not prior else prior['baseline_id'],
                 **body.model_dump(), 'updated_at': time.time(), 'actor': actor,
                 'scope': scope, 'project_id': report['project_id'], 'engine_id': report['engine_id']}
        self.db.execute('INSERT INTO accuracy_watches VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET body=excluded.body',
                        (scope, json.dumps(state, allow_nan=False)))
        return state

    def record(self, report):
        config = self.config(report)
        if not config or not config['enabled'] or config['baseline_id'] == report['id']:
            return
        baseline = self._report(config['baseline_id'])
        result = compare_reports(baseline, report, min_samples=config['min_samples'], min_drop=config['min_drop'])
        result['observed_at'] = time.time()
        result['trigger'] = 'new_reviewed_report'
        self.db.execute('INSERT INTO accuracy_observations VALUES (?,?)',
                        (report['id'], json.dumps(result, allow_nan=False)))

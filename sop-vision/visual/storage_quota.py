"""Upload admission quotas shared by stores; no worker or realtime-loop hooks."""
from contextlib import contextmanager
from functools import wraps
import json
import os
from pathlib import Path
import threading

from .storage_governance import usage_report

BUCKETS = frozenset({'labeling', 'documents', 'equipment-results'})


class QuotaExceeded(ValueError):
    pass


class StorageQuota:
    def __init__(self, data_dir: Path, limits=None):
        self.root = Path(data_dir).resolve()
        self.limits = dict(limits or {})
        for name, limit in self.limits.items():
            if name not in BUCKETS or type(limit) is not int or limit < 0:
                raise ValueError('存储配额仅支持 labeling/documents/equipment-results 的非负整数字节数')
        self._locks = {name: threading.RLock() for name in self.limits}

    @classmethod
    def from_env(cls, data_dir):
        raw = os.environ.get('STEPEYE_STORAGE_LIMITS_JSON', '').strip()
        limits = json.loads(raw) if raw else {}
        if not isinstance(limits, dict):
            raise ValueError('STEPEYE_STORAGE_LIMITS_JSON 必须是 JSON 对象')
        return cls(data_dir, limits)

    def _bucket(self, root):
        root = Path(root).resolve()
        return root.name if root.parent == self.root else None

    @contextmanager
    def operation(self, root):
        # Serialize admission and materialization, not decoding outside this store.
        lock = self._locks.get(self._bucket(root))
        if lock is None:
            yield
        else:
            with lock:
                yield

    def check(self, root, additional=0):
        name = self._bucket(root)
        if name not in self.limits:
            return
        used = usage_report(Path(root))['total_bytes']
        limit = self.limits[name]
        if used + additional > limit:
            raise QuotaExceeded(f'{name} 存储配额不足：已用 {used}，本次新增 {additional}，上限 {limit} 字节')


def quota_write(error_type):
    """Keep quota failures in each store's existing API error contract."""
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            if self.quota is None:
                return method(self, *args, **kwargs)
            try:
                with self.quota.operation(self.root):
                    return method(self, *args, **kwargs)
            except QuotaExceeded as exc:
                raise error_type(str(exc), 413) from None
        return wrapped
    return decorate

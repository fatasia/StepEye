"""Successful frames and fatal worker failures are separate runtime evidence."""
import pytest
from visual.dsl import StationConfig
from visual.runtime import StationRuntime


def runtime(tmp_path):
    cfg = StationConfig.model_validate({'station':'health-fixture','name':'health fixture',
        'source':{'type':'sim','width':320,'height':240,'realtime':False},
        'hands':{'enabled':False},
        'zones':[{'id':'post','polygon':[[0,0],[1,0],[1,1],[0,1]]}],
        'safety':{'enabled':True,'rules':[{'id':'idle','kind':'person_absent','zone':'post'}]}})
    return StationRuntime(cfg, tmp_path)


def test_successful_frame_health(tmp_path):
    rt = runtime(tmp_path)
    before = rt.health_snapshot()
    try:
        assert before['last_frame_at'] is None and before['error_total'] == 0
        rt.run(max_frames=2)
        after = rt.health_snapshot()
        assert after['epoch'] == before['epoch']
        assert after['last_frame_at'] is not None and not after['running']
        assert after['error_total'] == 0 and rt.frame_idx == 2
    finally:
        rt.close()


def test_fatal_source_error_not_a_successful_frame(tmp_path):
    rt = runtime(tmp_path)
    class Broken:
        def read(self): raise RuntimeError('source fixture failure')
        def close(self): pass
    rt.source.close()
    rt.source = Broken()
    try:
        with pytest.raises(RuntimeError, match='source fixture'):
            rt.run()
        health = rt.health_snapshot()
        assert health['error_total'] == 1 and not health['running']
        assert health['last_frame_at'] is None
    finally:
        rt.close()

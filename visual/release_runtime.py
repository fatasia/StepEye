"""Bind ledger identity to loaded runtime; sample only server-owned health."""
from pathlib import Path
from fastapi import HTTPException
from .model_release import validate_release_model


def bind_release(runtime, releases):
    runtime.release_id = None
    record = releases.active_for(runtime.cfg.station)
    detector = runtime.cfg.detector
    if not record or not detector.enabled:
        return
    if (Path(detector.model).resolve() != Path(record.get('model_path') or '').resolve()
            or detector.adapter != record['adapter']
            or list(detector.class_names) != record['classes']):
        return
    try:
        validate_release_model(record)
    except (ValueError, OSError):
        return
    runtime.release_id = record['id']


def sample_releases(manager):
    for scene in manager.release_monitor.active_scenes():
        runtime = manager.runtimes.get(scene)
        if runtime is None:
            continue
        state = manager.release_monitor.record(scene,
            runtime_release_id=getattr(runtime, 'release_id', None), **runtime.health_snapshot())
        if not state or state['status'] != 'pending_runtime_reload':
            continue
        active = manager.model_releases.active_for(scene)
        if not active or active['id'] != state['target_release_id']:
            continue
        # Active cycles cannot reload; avoid hashing large models on each pending tick.
        if runtime.engine.cycle is not None:
            continue
        # A changed/missing fallback must not replace the currently loaded runtime.
        try:
            validate_release_model(active)
            manager.reload(scene)
            restored = manager.runtimes[scene]
            if restored.release_id == active['id']:
                manager.release_monitor.runtime_applied(scene, active['id'])
                manager.hub.publish({'type':'event','station':scene,'kind':'release_auto_rollback',
                                     'data':{'release_id':active['id'],'reason':state['reason']}})
        except (HTTPException, ValueError, OSError):
            # A busy cycle remains pending and is retried by the existing heartbeat.
            continue

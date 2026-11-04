"""One request per process; trained feature+ExtraTrees ONNX graph, not linear extrapolation."""
import json,sys,time
from pathlib import Path
import numpy as np
from .mechanism_rul import RULBody,pinned_bundle,MODEL_SHA

def predict(spec):
    import onnxruntime as ort
    from .model_signature import verify_trust_set
    body=RULBody.model_validate(spec['body']);bundle=pinned_bundle(spec['root'])
    keys=spec.get('trusted_public_keys')
    signature={'verified':False,'status':'unsigned_research_asset'}
    if keys is not None:
        verify_trust_set(bundle,keys);signature={'verified':True,'status':'external_trust_verified','algorithm':'Ed25519'}
    manifest=json.loads((bundle/'manifest.json').read_text())
    options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
    started=time.perf_counter();session=ort.InferenceSession(str(bundle/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    values=np.asarray([w.values for w in body.windows],np.float32)
    if not np.isfinite(values).all():raise ValueError('Finite float32 values required')
    raw=session.run(None,{'pre/values':values})[0].reshape(-1)
    if len(raw)!=len(body.windows) or not np.isfinite(raw).all():raise ValueError('Invalid RUL output')
    prediction=np.clip(raw,0,125);radius=manifest['postprocess']['interval_radius_cycles']
    results=[{'unit_id':w.unit_id,'end_cycle':w.end_cycle,'rul_cycles':float(p),
              'interval_cycles':[max(0,float(p)-radius),float(p)+radius]} for w,p in zip(body.windows,prediction)]
    pinned_bundle(spec['root'])
    return {'kind':'cmapss_rul','status':'evidence_only','method':'trained_fd001_feature_regressor',
            'model_sha256':MODEL_SHA,'unit':'cycle','dataset':body.dataset,'results':results,
            'scope':'NASA C-MAPSS FD001 simulated turbofan; not general motor/equipment RUL',
            'interval_method':'90% split-conformal residual radius from 15 separate calibration engines; exchangeability assumed',
            'signature':signature,'elapsed_ms':(time.perf_counter()-started)*1000}

if __name__=='__main__':
    try:
        if len(sys.argv)!=3 or sys.argv[1]!='--request':raise ValueError('Private request file required')
        with Path(sys.argv[2]).open('rb') as stream:raw=stream.read(512*1024+1)
        if len(raw)>512*1024:raise ValueError('Input limit')
        print(json.dumps(predict(json.loads(raw)),allow_nan=False))
    except Exception:
        print(json.dumps({'error':'rul_worker_failed'}));raise SystemExit(1)

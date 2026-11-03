"""Real ORT constant graph + frozen human-reviewed labels: software protocol evidence."""
import hashlib
import io
import json
import zipfile

from PIL import Image
import pytest
from visual.labels import LabelStore
from visual.detection_holdout import run_holdout, sha_file
from test_detector_runtime_adapter import graph


def dataset(tmp_path):
    store=LabelStore(tmp_path/'labels')
    try:
        p=store.create_project('protocol',['part','other'])
        for i in range(8):
            stream=io.BytesIO();Image.new('RGB',(640,640),(i*25,40,60)).save(stream,format='PNG')
            a=store.add_asset(p['id'],stream.getvalue(),f'{i}.png',{'group_id':f'camera-{i}'})
            if i%2:
                a=store.save_draft(a['id'],[{'label_id':0,'xyxy_norm':[.25,.25,.375,.375]}],a['revision'],'tester')
            store.confirm(a['id'],a['revision'],'reviewer',background=not i%2)
        v=store.create_version(p['id'],seed=42,val_ratio=.5,actor='reviewer')
        return store.export_path(p['id'],v['id'])
    finally:store.close()


def arguments(tmp_path):
    model=graph(tmp_path);data=dataset(tmp_path)
    return dict(dataset=data,model=model,output=tmp_path/'report.json',
                model_sha256=sha_file(model),dataset_sha256=sha_file(data),adapter='yolo_e2e')


def test_real_onnx_version_to_report(tmp_path):
    args=arguments(tmp_path);report=run_holdout(**args)
    assert report['evaluation']['frames']==4
    assert report['evaluation']['summary']['fp']>=4 # second class has no ground truth
    assert report['evaluation']['summary']['tp']>0
    assert any(s['state']=='background' for s in report['sources'])
    assert report['split']=='val' and report['publication']=='not_requested'
    assert json.loads(args['output'].read_text(encoding='utf8'))==report
    with pytest.raises(FileExistsError):run_holdout(**args)


def rewrite(args,edit):
    with zipfile.ZipFile(args['dataset']) as z:files={n:z.read(n) for n in z.namelist()}
    m=json.loads(files['manifest.json']);meta=json.loads(files['metadata.json']);edit(m,meta,files)
    raw=json.dumps(m).encode();sha=hashlib.sha256(raw).hexdigest();files['manifest.json']=raw
    meta.update(manifest_sha256=sha,dataset_version_id='LV-'+sha[:32]);files['metadata.json']=json.dumps(meta).encode()
    target=args['output'].parent/'edited.zip'
    with zipfile.ZipFile(target,'w') as z:
        for n,b in files.items():z.writestr(n,b)
    args.update(dataset=target,dataset_sha256=sha_file(target))


@pytest.mark.parametrize('fault',['sha','negative','leak','image','unconfirmed'])
def test_reject_corrupt_or_unreviewed_inputs(tmp_path,fault):
    args=arguments(tmp_path)
    if fault=='sha':args['model_sha256']='0'*64
    else:
        def edit(m,meta,files):
            val=[a for a in m['assets'] if m['assignments'][a['id']]['split']=='val']
            if fault=='negative':meta['reviewed_negatives']=[]
            if fault=='image':files[f"images/val/{val[0]['id']}.png"]=b'bad'
            if fault=='unconfirmed':
                for a in val:a['state']='draft'
            if fault=='leak':
                train=next(a for a in m['assets'] if m['assignments'][a['id']]['split']=='train')
                val[0]['group_id']=train['group_id']
        rewrite(args,edit)
    with pytest.raises(ValueError):run_holdout(**args)
    assert not args['output'].exists()


def test_cli_writes_report(tmp_path):
    import subprocess, sys
    from pathlib import Path
    args=arguments(tmp_path)
    command=[sys.executable,str(Path(__file__).resolve().parents[1]/'tools/evaluate_detection_holdout.py')]
    for key,value in args.items():command.extend(['--'+key.replace('_','-'),str(value)])
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['summary']['fp']>=4

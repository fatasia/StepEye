"""Verified official VideoMAEv2-small K710 checkpoint -> fixed StepEye ONNX."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
import time

WEIGHT_SHA='24fb71687fa3671b8387cadfbcbab0f72af695692e93cf1ecc82caa888626172'
CODE_SHA='d132c97369f25207e380e78c59294fd00b93d2f6aa0fb5c4ded19eff26a5a3cb'
LABEL_SHA='21252255bdf4e69af20eecf81fc4a8fd7feb34e0388b0edcc0fbe8d5d333af1f'


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def text_digest(path):
    # Git Windows checkout may use CRLF; only normalize line endings, not content.
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def verified_labels(path):
    if text_digest(path)!=LABEL_SHA:raise ValueError('Official K710 label map mismatch')
    names=Path(path).read_text(encoding='utf-8').splitlines()
    if len(names)!=710 or len(set(names))!=710 or any(not n for n in names):
        raise ValueError('Incomplete classification labels')
    return names


def load_model(checkpoint,source):
    if digest(checkpoint)!=WEIGHT_SHA:raise ValueError('Official checkpoint hash mismatch')
    code=Path(source)/'models/modeling_finetune.py'
    if text_digest(code)!=CODE_SHA:raise ValueError('Reviewed official model source mismatch')
    import torch
    torch.set_num_threads(2)
    weights=torch.load(checkpoint,map_location='cpu',weights_only=True)['module']
    if tuple(weights['head.weight'].shape)!=(710,384) or tuple(weights['head.bias'].shape)!=(710,):
        raise ValueError('Missing trained K710 classifier head')
    spec=importlib.util.spec_from_file_location('stepeye_videomaev2_official',code)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    model=module.vit_small_patch16_224(num_classes=710,all_frames=16).eval()
    model.load_state_dict(weights,strict=True)
    return model


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--clip',type=Path,required=True,help='already normalized float32 BCTHW NPY')
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():p.error('refusing to overwrite evidence')
    labels=verified_labels(args.source/'misc/label_map_k710.txt')
    import torch,numpy as np
    array=np.load(args.clip,allow_pickle=False)
    if array.shape!=(1,3,16,224,224) or array.dtype!=np.float32 or not np.isfinite(array).all():
        raise ValueError('Expected finite normalized [1,3,16,224,224] float32 clip')
    model=load_model(args.checkpoint,args.source)
    sample=torch.from_numpy(array)
    start=time.perf_counter()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with torch.inference_mode():
        expected=model(sample).numpy()
        torch.onnx.export(model,sample,str(args.out),input_names=['clip'],output_names=['logits'],
                          opset_version=17,dynamo=False,external_data=False)
    import onnxruntime as ort
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    session=ort.InferenceSession(str(args.out),sess_options=options,providers=['CPUExecutionProvider'])
    if session.get_inputs()[0].shape!=[1,3,16,224,224] or session.get_outputs()[0].shape!=[1,710]:
        raise ValueError('Export shape mismatch')
    actual=session.run(None,{'clip':array})[0]
    error=float(np.max(np.abs(actual-expected)))
    if not np.allclose(expected,actual,atol=1e-3,rtol=1e-3):raise ValueError('ONNX parity mismatch')
    args.out.with_suffix('.classes.json').write_text(json.dumps(labels),encoding='utf-8')
    report={'architecture':'VideoMAEv2 ViT-small K710 distilled classifier','source_sha256':WEIGHT_SHA,
            'code_revision':'29eab1e8a588d1b3ec0cdec7b03a86cca491b74b',
            'weight_revision':'706cc172d65ebd4dedbee3f9c0183a93df9fa125',
            'onnx_sha256':digest(args.out),'input':[1,3,16,224,224],'output':[1,710],
            'parity_max_abs':error,'torch_top_class':labels[int(expected.argmax())],
            'onnx_top_class':labels[int(actual.argmax())],'export_s':time.perf_counter()-start,
            'code_license':'MIT','weight_card_license':'Apache-2.0','validation':'single_public_clip_not_industrial_accuracy'}
    args.out.with_suffix('.export.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()

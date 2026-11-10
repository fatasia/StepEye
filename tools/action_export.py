"""固定 VideoMAE K400 safetensors → StepEye BCTHW ONNX，禁止特征模型冒充分类器。"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time

SOURCE_SHA256='065cfde56f97671da4d196e1a3a6d3cf9304dcad92eb671e3632a6c6cdd04f73'
REVISION='488eb9a0565f257b32866000305c8178965eb9f6'


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def class_map(config):
    if (config.get('architectures')!=['VideoMAEForVideoClassification']
            or config.get('num_frames')!=16 or config.get('num_channels')!=3
            or config.get('model_type')!='videomae'):
        raise ValueError('Expected full VideoMAE 16-frame classifier')
    mapping=config.get('id2label',{})
    if set(mapping)!={str(i) for i in range(400)}:raise ValueError('K400 class map incomplete')
    labels=[mapping[str(i)] for i in range(400)]
    if len(set(labels))!=400 or not all(isinstance(x,str) and 0<len(x)<=100 for x in labels):
        raise ValueError('Invalid class labels')
    return labels


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('output exists; refusing overwrite')
    model_path=a.source/'model.safetensors'
    if sha256(model_path)!=SOURCE_SHA256:raise ValueError('Official checkpoint hash mismatch')
    config=json.loads((a.source/'config.json').read_text(encoding='utf-8'))
    labels=class_map(config)
    import torch
    from safetensors import safe_open
    from safetensors.torch import load_file
    from transformers import VideoMAEConfig,VideoMAEForVideoClassification
    with safe_open(model_path,framework='pt',device='cpu') as f:
        if list(f.get_slice('classifier.weight').get_shape())!=[400,768]:
            raise ValueError('Missing or incompatible trained classification head')
    torch.set_num_threads(2)
    cfg=VideoMAEConfig.from_dict(config)
    cfg._attn_implementation='eager'
    model=VideoMAEForVideoClassification(cfg).eval()
    model.load_state_dict(load_file(model_path),strict=True)
    class Adapter(torch.nn.Module):
        def __init__(self,model):super().__init__();self.model=model
        def forward(self,x):return self.model(pixel_values=x.permute(0,2,1,3,4)).logits
    wrapped=Adapter(model).eval()
    sample=torch.zeros((1,3,16,224,224),dtype=torch.float32)
    started=time.perf_counter()
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with torch.inference_mode():
        expected=wrapped(sample).numpy()
        torch.onnx.export(wrapped,sample,str(a.out),input_names=['clip'],output_names=['logits'],
                          opset_version=17,dynamo=False,external_data=False)
    import onnxruntime as ort
    import numpy as np
    options=ort.SessionOptions();options.intra_op_num_threads=2
    session=ort.InferenceSession(str(a.out),sess_options=options,providers=['CPUExecutionProvider'])
    if session.get_inputs()[0].shape!=[1,3,16,224,224] or session.get_outputs()[0].shape!=[1,400]:
        raise ValueError('Export contract mismatch')
    actual=session.run(None,{'clip':sample.numpy()})[0]
    error=float(np.max(np.abs(actual-expected)))
    if not np.allclose(actual,expected,atol=1e-3,rtol=1e-3):raise ValueError('ONNX parity failed')
    classes=a.out.with_suffix('.classes.json');classes.write_text(json.dumps(labels),encoding='utf-8')
    report={'source_revision':REVISION,'source_sha256':SOURCE_SHA256,'onnx_sha256':sha256(a.out),
            'head_shape':[400,768],'input':[1,3,16,224,224],'output':[1,400],
            'zero_input_parity_max_abs':error,'elapsed_s':time.perf_counter()-started,
            'license':'CC-BY-NC-4.0','deployment':'evaluation_only',
            'preprocessing_note':'StepEye short-side256 center224 differs from official processor size224; no accuracy equivalence claim'}
    a.out.with_suffix('.export.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()

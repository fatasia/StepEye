"""Single-shape PyTorch CUDA evidence; not the ONNX runtime deployment benchmark."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
from action_export_v2 import load_model,verified_labels,WEIGHT_SHA


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--clip',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():p.error('evidence already exists')
    import torch
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable')
    array=np.load(args.clip,allow_pickle=False)
    if array.shape!=(1,3,16,224,224) or array.dtype!=np.float32:raise ValueError('fixed normalized clip required')
    names=verified_labels(args.source/'misc/label_map_k710.txt')
    start=time.perf_counter()
    model=load_model(args.checkpoint,args.source).cuda().eval()
    x=torch.from_numpy(array).cuda();torch.cuda.synchronize()
    load_ms=(time.perf_counter()-start)*1000
    torch.cuda.reset_peak_memory_stats()
    times=[]
    with torch.inference_mode():
        for _ in range(6):
            torch.cuda.synchronize();tick=time.perf_counter()
            logits=model(x);torch.cuda.synchronize()
            times.append((time.perf_counter()-tick)*1000)
    scores=torch.softmax(logits[0],dim=0);values,indices=torch.topk(scores,5)
    report={'runtime':'PyTorch CUDA, not ONNX Runtime CUDA','device':torch.cuda.get_device_name(0),
            'torch':torch.__version__,'checkpoint_sha256':WEIGHT_SHA,'input':[1,3,16,224,224],
            'dtype':'float32','load_and_transfer_ms':load_ms,'first_inference_ms':times[0],
            'cold_load_to_result_ms':load_ms+times[0],'warm_runs':5,
            'warm_p50_ms':float(np.percentile(times[1:],50)),'warm_p95_ms':float(np.percentile(times[1:],95)),
            'peak_allocated_mib':torch.cuda.max_memory_allocated()/1024**2,
            'peak_reserved_mib':torch.cuda.max_memory_reserved()/1024**2,
            'top_k':[{'label':names[int(i)],'class_id':int(i),'confidence':float(v)} for i,v in zip(indices,values)],
            'scope':'single public clip; excludes sampling/queue; not industrial accuracy or ONNX CUDA validation'}
    args.out.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
    del x,model,logits,scores,values,indices
    torch.cuda.empty_cache()


if __name__=='__main__':main()

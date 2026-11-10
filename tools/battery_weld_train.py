"""Fixed224 grayscale EV-weld classification pilot, random initialization, no external weights."""
from pathlib import Path
import argparse,csv,io,json,random,time,zipfile,hashlib
import numpy as np
import torch
from torch import nn
from torch.utils.data import Dataset,DataLoader
from PIL import Image


class WeldCNN(nn.Module):
    def __init__(self):
        super().__init__();layers=[];previous=1
        for channels in (16,32,64,96):
            layers += [nn.Conv2d(previous,channels,3,stride=2,padding=1),nn.BatchNorm2d(channels),nn.ReLU()]
            previous=channels
        self.features=nn.Sequential(*layers,nn.AdaptiveAvgPool2d(1))
        self.head=nn.Linear(96,2)
    def forward(self,x):return self.head(self.features(x).flatten(1))


class Images(Dataset):
    def __init__(self,archive,rows,augment=False):self.archive=archive;self.rows=rows;self.augment=augment;self.zip=None
    def __len__(self):return len(self.rows)
    def __getitem__(self,index):
        if self.zip is None:self.zip=zipfile.ZipFile(self.archive)
        row=self.rows[index]
        with Image.open(io.BytesIO(self.zip.read(row['member']))) as image:
            image=image.convert('L').resize((224,224),Image.Resampling.BILINEAR)
            if self.augment and random.random()<.5:image=image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            x=np.asarray(image,dtype=np.float32).copy()[None]/255
        return torch.from_numpy(x),int(row['label']=='KO')
    def close(self):
        if self.zip is not None:self.zip.close();self.zip=None


def choose(rows,limit,seed):
    if not limit or len(rows)<=limit:return rows
    rng=random.Random(seed);groups=[[r for r in rows if r['label']==label] for label in ('OK','KO')]
    selected=[]
    for group in groups:
        rng.shuffle(group);selected+=group[:max(1,round(limit*len(group)/len(rows))) ]
    rng.shuffle(selected);return selected


def select_members(rows,args):
    exclusion=getattr(args,'exclude_test_members',None);provenance={}
    if exclusion:
        path=Path(exclusion)
        if path.stat().st_size>20*1024*1024:raise ValueError('exclusion file too large')
        raw=path.read_bytes();previous=json.loads(raw)
        if not isinstance(previous,dict) or not isinstance(previous.get('test'),list):raise ValueError('exclusion requires test member list')
        if any(not isinstance(r,dict) or r.get('split')!='test' or not isinstance(r.get('member'),str) for r in previous['test']):raise ValueError('invalid test exclusion record')
        excluded={r['member'] for r in previous['test']}
        if not excluded.issubset({r['member'] for r in rows if r['split']=='test'}):raise ValueError('excluded member absent from official test')
        rows=[r for r in rows if r['split']!='test' or r['member'] not in excluded]
        provenance={'exclude_test_file':str(path.resolve()),'exclude_test_sha256':hashlib.sha256(raw).hexdigest(),'excluded_test_count':len(excluded)}
    selected={s:choose([r for r in rows if r['split']==s],args.max_train if s=='train' else args.max_eval,42) for s in ('train','val','test')}
    if any(not v or len({r['label'] for r in v})!=2 for v in selected.values()):raise ValueError('all splits require both labels')
    return selected,provenance


def load_initial(model,path):
    if not path:return {'kind':'random','sha256':None}
    path=Path(path)
    if not path.is_file() or path.stat().st_size>20*1024*1024:raise ValueError('initial state must be a local state_dict <=20MB')
    raw=path.read_bytes();state=torch.load(io.BytesIO(raw),weights_only=True,map_location='cpu')
    if not isinstance(state,dict) or any(not isinstance(v,torch.Tensor) or not torch.isfinite(v).all() for v in state.values()):raise ValueError('initial state requires finite tensors')
    model.load_state_dict(state,strict=True)
    return {'kind':'explicit_local_state_dict','path':str(path.resolve()),'sha256':hashlib.sha256(raw).hexdigest(),'architecture':'WeldCNN 1/16/32/64/96 channels, 2 classes'}


@torch.no_grad()
def evaluate(model,loader,device):
    model.eval();cm=np.zeros((2,2),dtype=int)
    for x,y in loader:
        pred=model(x.to(device)).argmax(1).cpu().numpy()
        for actual,p in zip(y.numpy(),pred):cm[actual,p]+=1
    tn,fp,fn,tp=cm.ravel();total=cm.sum()
    return {'n':int(total),'confusion_matrix_OK_KO':cm.tolist(),'accuracy':float((tn+tp)/total),
            'KO_recall':float(tp/(tp+fn)) if tp+fn else None,
            'KO_precision':float(tp/(tp+fp)) if tp+fp else None,
            'false_accept_KO_rate':float(fn/(tp+fn)) if tp+fn else None}


def _train_impl(args,datasets):
    torch.manual_seed(42);np.random.seed(42);random.seed(42);torch.set_num_threads(2)
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    with Path(args.index).open(encoding='utf8') as source:rows=list(csv.DictReader(source))
    selected,provenance=select_members(rows,args)
    (out/'selected-members.json').write_text(json.dumps(selected,indent=2),encoding='utf8')
    datasets.update({s:Images(args.archive,v,s=='train') for s,v in selected.items()})
    loaders={s:DataLoader(d,batch_size=args.batch,shuffle=s=='train',num_workers=args.workers,persistent_workers=False) for s,d in datasets.items()}
    device='cuda' if torch.cuda.is_available() else 'cpu';model=WeldCNN()
    initialization=load_initial(model,getattr(args,'initial_state',None));model=model.to(device)
    baseline_test=evaluate(model,loaders['test'],device) if getattr(args,'initial_state',None) else None
    counts=np.bincount([int(r['label']=='KO') for r in selected['train']],minlength=2)
    weights=torch.tensor(len(selected['train'])/(2*counts),dtype=torch.float32,device=device)
    criterion=nn.CrossEntropyLoss(weight=weights);optimizer=torch.optim.AdamW(model.parameters(),lr=.001)
    history=[];best=-1;best_epoch=None;start=time.perf_counter()
    for epoch in range(args.epochs):
        model.train();loss_sum=0
        for x,y in loaders['train']:
            x=x.to(device);y=y.to(device);optimizer.zero_grad();loss=criterion(model(x),y);loss.backward();optimizer.step();loss_sum+=loss.item()*len(y)
        metrics=evaluate(model,loaders['val'],device);history.append({'epoch':epoch+1,'loss':loss_sum/len(datasets['train']),'val':metrics})
        cm=np.asarray(metrics['confusion_matrix_OK_KO']);balanced=float(np.mean(np.diag(cm)/cm.sum(1)))
        if balanced>best:best=balanced;best_epoch=epoch+1;torch.save(model.state_dict(),out/'best-state.pt')
        print(json.dumps(history[-1]),flush=True)
    model.load_state_dict(torch.load(out/'best-state.pt',weights_only=True,map_location=device))
    test=evaluate(model,loaders['test'],device);model=model.cpu().eval();example=datasets['test'][0][0][None]
    with torch.no_grad():reference=model(example).numpy()
    torch.onnx.export(model,example,str(out/'battery-weld-cnn.onnx'),opset_version=17,input_names=['images'],output_names=['logits'],dynamo=False)
    import onnxruntime as ort
    options=ort.SessionOptions();options.intra_op_num_threads=2
    session=ort.InferenceSession(str(out/'battery-weld-cnn.onnx'),options,providers=['CPUExecutionProvider'])
    t=time.perf_counter();actual=session.run(None,{'images':example.numpy()})[0];latency=(time.perf_counter()-t)*1000
    probs=np.exp(actual-actual.max());probs/=probs.sum()
    report={'classes':['OK','KO'],'device':device,'initialization':initialization,'baseline_test_same_members':baseline_test,'member_provenance':provenance,
            'selected_members_sha256':hashlib.sha256((out/'selected-members.json').read_bytes()).hexdigest(),
            'index_sha256':hashlib.sha256(Path(args.index).read_bytes()).hexdigest(),'input':[1,1,224,224],
            'preprocess':'PIL L, bilinear direct resize224x224, float32/255; no padding/shape metadata',
            'counts':{s:len(v) for s,v in selected.items()},'selected_epoch':best_epoch,'selection':'validation balanced accuracy','epochs':args.epochs,'batch':args.batch,'history':history,'test':test,
            'elapsed_s':time.perf_counter()-start,'onnx_sha256':hashlib.sha256((out/'battery-weld-cnn.onnx').read_bytes()).hexdigest(),
            'probe':{'member':selected['test'][0]['member'],'true_label':selected['test'][0]['label'],'predicted_label':['OK','KO'][int(actual.argmax())],
                     'probabilities':probs.tolist(),'cpu_first_infer_ms':latency,'torch_onnx_max_abs':float(np.max(np.abs(reference-actual)))},
            'scope':'official split experiment, exact hashes deduplicated; physical workpiece grouping unknown; resize does not prove removal of acquisition shortcuts',
            'production_approved':False}
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report),flush=True)



def train(args):
    limits={'epochs':(1,20),'batch':(1,256),'workers':(0,4),'max_train':(2,50000),'max_eval':(2,10000)}
    if any(type(getattr(args,key)) is not int or not low<=getattr(args,key)<=high
           for key,(low,high) in limits.items()):raise ValueError('bounded training options required')
    if Path(args.out).exists():raise FileExistsError('training output already exists; choose a new directory')
    datasets={}
    try:return _train_impl(args,datasets)
    finally:
        for dataset in datasets.values():dataset.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('archive');p.add_argument('--index',required=True);p.add_argument('--out',required=True)
    p.add_argument('--epochs',type=int,default=3);p.add_argument('--batch',type=int,default=64);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--max-train',type=int,default=6000);p.add_argument('--max-eval',type=int,default=2000)
    p.add_argument('--initial-state');p.add_argument('--exclude-test-members')
    a=p.parse_args()
    train(a)

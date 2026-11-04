"""Prepare pinned DsPCBSD+ COCO boxes for the existing training worker."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import zipfile
import yaml

SOURCE_SHA = 'b256846a1f2cf17e6e88be49c551ebf2c7551601e08691f50c70dc367a218817'
CLASSES = ['SH','SP','SC','OP','MB','HB','CS','CFO','BMFO']


def clipped_box(box, width, height):
    x,y,w,h = box
    if not all(math.isfinite(v) for v in box) or w<=0 or h<=0:
        raise ValueError('Invalid box')
    original=[x,y,x+w,y+h]
    clipped=[max(0.,x),max(0.,y),min(float(width),x+w),min(float(height),y+h)]
    if max(abs(a-b) for a,b in zip(original,clipped))>.02:
        raise ValueError('Box exceeds audited rounding tolerance')
    x0,y0,x1,y1=clipped
    if x0>=x1 or y0>=y1:raise ValueError('Empty clipped box')
    return [(x0+x1)/2/width,(y0+y1)/2/height,(x1-x0)/width,(y1-y0)/height], original!=clipped


def prepare(archive, output, expected_sha=SOURCE_SHA):
    archive,output=Path(archive),Path(output)
    if output.exists():raise ValueError('Output exists; preserve prior versions')
    with archive.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    if digest!=expected_sha:raise ValueError('Source SHA mismatch')
    rows=[];corrections=[];seen={};counts=defaultdict(Counter)
    with zipfile.ZipFile(archive) as z:
        for author_split in ('train','val'):
            data=json.loads(z.read(f'Data_COCO/annotations/instances_{author_split}2017.json'))
            if sorted(data['categories'],key=lambda c:c['id'])!=[{'id':i+1,'name':n,'supercategory':'None'} for i,n in enumerate(CLASSES)]:
                raise ValueError('Unexpected categories')
            annotations=defaultdict(list)
            for a in data['annotations']:annotations[a['image_id']].append(a)
            for im in data['images']:
                name=im['file_name']
                if PurePosixPath(name).name!=name or '\\' in name:raise ValueError('Unsafe image name')
                raw=z.read(f'Data_COCO/{author_split}2017/{name}')
                sha=hashlib.sha256(raw).hexdigest()
                split='test' if author_split=='val' else 'val' if int(sha[:8],16)%10==0 else 'train'
                if sha in seen and seen[sha]!=split:raise ValueError('Cross-split duplicate')
                seen[sha]=split;lines=[]
                for a in annotations[im['id']]:
                    cid=a['category_id']-1
                    if not 0<=cid<len(CLASSES):raise ValueError('Invalid class')
                    values,changed=clipped_box(a['bbox'],im['width'],im['height'])
                    if changed:corrections.append({'author_split':author_split,'image':name,'annotation_id':a['id'],'original_xywh':a['bbox'],'normalized_clipped_xywh':values})
                    lines.append(str(cid)+' '+' '.join(f'{v:.10f}' for v in values));counts[split][CLASSES[cid]]+=1
                if not lines:raise ValueError('Unreviewed negative image')
                rows.append((split,author_split,name,sha,raw,lines,im['width'],im['height']))
        for split in ('train','val','test'):
            if set(counts[split])!=set(CLASSES):raise ValueError('Split lacks classes')
        result={'source':'https://doi.org/10.6084/m9.figshare.24970329.v1','source_sha256':digest,'license':'CC-BY-4.0','classes':CLASSES,
                'split_method':'author val retained as test; SHA256 first32bits modulo10==0 selects validation from author train',
                'group_limit':'No verified board/session identifiers; not board-isolated, near-duplicates remain possible',
                'corrections':corrections,'images':[],'splits':{}}
        for split,author,name,sha,raw,lines,width,height in rows:
            image=output/'images'/split/name;label=output/'labels'/split/Path(name).with_suffix('.txt')
            image.parent.mkdir(parents=True,exist_ok=True);label.parent.mkdir(parents=True,exist_ok=True)
            if image.exists() or label.exists():raise ValueError('Duplicate filename')
            image.write_bytes(raw);label.write_text('\n'.join(lines)+'\n',encoding='utf8')
            result['images'].append({'file':name,'split':split,'author_split':author,'source_sha256':sha,'width':width,'height':height})
        for split in ('train','val','test'):
            result['splits'][split]={'images':sum(r[0]==split for r in rows),'boxes':dict(counts[split])}
        (output/'dataset.yaml').write_text(yaml.safe_dump({'path':'.','train':'images/train','val':'images/val','names':CLASSES}),encoding='utf8')
        (output/'metadata.json').write_text(json.dumps({'dataset_version_id':'DSPCBSD-V1-SHA10','source_license':'CC-BY-4.0','source_sha256':digest}),encoding='utf8')
        (output/'source.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    r=prepare(**vars(p.parse_args()));print(json.dumps({'splits':r['splits'],'clipped_boxes':len(r['corrections'])}))

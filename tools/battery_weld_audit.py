"""Verify OPENZDM EV weld v1 ZIP, audit splits, and build a hash-deduplicated index."""
from pathlib import Path
import argparse,collections,csv,hashlib,io,json,zipfile
from PIL import Image

EXPECTED_SIZE=1153183844
EXPECTED_MD5='f187dac063cf656bb627327fa9771d16'


def audit(archive,out):
    archive=Path(archive);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    if archive.stat().st_size!=EXPECTED_SIZE:raise ValueError('incomplete archive')
    digest=hashlib.md5();sha=hashlib.sha256()
    with archive.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block);sha.update(block)
    if digest.hexdigest()!=EXPECTED_MD5:raise ValueError('archive MD5 mismatch')
    rows=[];counts=collections.Counter();groups=collections.defaultdict(list)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir():continue
            parts=info.filename.split('/')
            if len(parts)!=3 or parts[0] not in ('train','val','test') or parts[1] not in ('OK','KO'):
                raise ValueError('unexpected member '+info.filename)
            if info.file_size>2_000_000:raise ValueError('oversize image')
            raw=z.read(info)  # zipfile verifies each CRC; no filesystem extraction.
            with Image.open(io.BytesIO(raw)) as im:
                im.load();w,h=im.size;mode=im.mode
            record={'member':info.filename,'split':parts[0],'label':parts[1],
                    'sha256':hashlib.sha256(raw).hexdigest(),'width':w,'height':h,'mode':mode}
            rows.append(record);groups[record['sha256']].append(record);counts[parts[0]+'/'+parts[1]]+=1
    rank={'train':0,'val':1,'test':2};retained=[];duplicate_groups=[];conflicts=0
    for digest,items in groups.items():
        if len({v['label'] for v in items})>1:
            conflicts+=1;duplicate_groups.append({'sha256':digest,'conflict':True,'members':[v['member'] for v in items]});continue
        items.sort(key=lambda x:(rank[x['split']],x['member']))
        retained.append(items[0])
        if len(items)>1:duplicate_groups.append({'sha256':digest,'conflict':False,'members':[v['member'] for v in items]})
    def write_csv(name,values):
        with (out/name).open('w',newline='',encoding='utf8') as f:
            writer=csv.DictWriter(f,fieldnames=['member','split','label','sha256','width','height','mode']);writer.writeheader();writer.writerows(values)
    write_csv('all-images.csv',rows);write_csv('deduplicated-images.csv',retained)
    (out/'duplicate-groups.json').write_text(json.dumps(duplicate_groups,indent=2),encoding='utf8')
    import numpy as np
    sizes={}
    for key in counts:
        subset=[r for r in rows if r['split']+'/'+r['label']==key]
        sizes[key]={axis:{'min':min(r[axis] for r in subset),'median':float(np.median([r[axis] for r in subset])),
                         'max':max(r[axis] for r in subset)} for axis in ('width','height')}
    ko=sizes['train/KO']['width']['median'];ok=sizes['train/OK']['width']['median'];threshold=(ko+ok)/2
    shape_scores={}
    for split in ('val','test'):
        subset=[r for r in retained if r['split']==split]
        correct=sum(('KO' if (r['width']>threshold)==(ko>ok) else 'OK')==r['label'] for r in subset)
        per_label={label:sum(('KO' if (r['width']>threshold)==(ko>ok) else 'OK')==label
                                      for r in subset if r['label']==label)/sum(r['label']==label for r in subset)
                   for label in ('OK','KO') if any(r['label']==label for r in subset)}
        shape_scores[split]={'n':len(subset),'accuracy':correct/len(subset) if subset else None,
                             'recall_by_label':per_label,'balanced_accuracy':float(np.mean(list(per_label.values()))),
                             'majority_baseline_accuracy':max(sum(r['label']==label for r in subset) for label in ('OK','KO'))/len(subset) if subset else None}
    report={'archive':str(archive.resolve()),'bytes':EXPECTED_SIZE,'md5':EXPECTED_MD5,'sha256':sha.hexdigest(),
            'crc_and_decode_verified':len(rows),'official_counts':dict(counts),'retained_counts':dict(collections.Counter(r['split']+'/'+r['label'] for r in retained)),
            'duplicate_groups':len(duplicate_groups),'cross_split_duplicate_groups':sum(len({r['split'] for r in items})>1 for items in groups.values()),
            'conflicting_label_groups_excluded':conflicts,'size_statistics':sizes,
            'train_width_only_threshold':threshold,'width_only_holdout':shape_scores,
            'grouping':'original physical weld/cell IDs unknown; exact hash filtering is not group independence',
            'dedup_policy':'conflicting labels excluded; earliest train/val/test member retained; other exact copies excluded'}
    (out/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('archive');p.add_argument('--out',required=True);a=p.parse_args();audit(a.archive,a.out)

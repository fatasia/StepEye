"""按CoatingVision原始R编号隔离批次；缺标不补背景，公开标签不冒充本地人工复核。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import zipfile

from PIL import Image
import yaml

SOURCE_SHA = "04dd29cac2124ce08410225fbec93ef2e1a91af790cf24a769e29cf316e76655"
SOURCE_DOI = "https://doi.org/10.6084/m9.figshare.29260121.v1"
CLASS_NAMES = ["pinhole", "unclassified"]


def fingerprint(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""): h.update(block)
    return h.hexdigest()


def prepare(archive: Path, output: Path, *, mode="two-class", train_groups=("R1",), val_groups=("R7",),
            expected_sha=SOURCE_SHA):
    archive, output = Path(archive).resolve(), Path(output).resolve()
    if output.exists(): raise ValueError("输出目录必须不存在，禁止覆盖历史数据")
    if mode not in {"two-class", "pinhole"}: raise ValueError("未知任务范围")
    train_groups, val_groups = set(train_groups), set(val_groups)
    if not train_groups or not val_groups or train_groups & val_groups:
        raise ValueError("train/val必须使用非空且不相交的源批次")
    if any(not re.fullmatch(r"R\d+", v) for v in train_groups | val_groups):
        raise ValueError("批次必须为原始R编号，不允许相邻帧随机拆分")
    digest = fingerprint(archive)
    if digest != expected_sha: raise ValueError("源压缩包SHA256不匹配")
    selected = [0,1] if mode == "two-class" else [0]
    names = [CLASS_NAMES[i] for i in selected]
    pairs, missing, outside_scope, used_hashes = [], [], [], {}
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos)>20000 or sum(i.file_size for i in infos)>512*1024*1024:
            raise ValueError("源包超过CoatingVision转换限制")
        if len({i.filename for i in infos}) != len(infos): raise ValueError("ZIP包含重复路径")
        for info in infos:
            p = PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts or "\\" in info.filename or ":" in info.filename:
                raise ValueError("ZIP路径无效")
        if z.testzip(): raise ValueError("ZIP CRC校验失败")
        index = {}
        for row in csv.DictReader(io.StringIO(z.read("classification/labels.csv").decode("utf-8-sig"))):
            name, original = row["file_name"], row["original_file_name"]
            if Path(name).name != name or not re.fullmatch(r"image_\d+\.jpg",name) or name in index:
                raise ValueError("分类清单文件名无效或重复")
            group = re.match(r"^(R\d+)-", original)
            if not group: raise ValueError("无法从原始文件名确定源批次")
            index[name] = (group[1], original)
        files = set(z.namelist())
        for name, (group, original) in sorted(index.items()):
            image, label = "detection/images/"+name, "detection/labels/"+Path(name).with_suffix(".txt").name
            if image not in files: raise ValueError("清单图片缺失")
            if label not in files:
                missing.append(name); continue
            lines = []
            for raw in z.read(label).decode("utf-8-sig").splitlines():
                if not raw.strip(): continue
                fields = raw.split()
                if len(fields)!=5 or fields[0] not in {"0","1"}: raise ValueError("非预期YOLO标签类别/字段")
                cid = int(fields[0]);x,y,w,h = map(float,fields[1:])
                if (not all(math.isfinite(v) for v in [x,y,w,h]) or not 0<w<=1 or not 0<h<=1 or
                    min(x-w/2,y-h/2)<-1e-9 or max(x+w/2,y+h/2)>1+1e-9):
                    raise ValueError("非法归一化框")
                if cid in selected: lines.append((selected.index(cid),[x,y,w,h]))
            if not lines:
                outside_scope.append(name); continue  # 不是已复核的空背景。
            split = "train" if group in train_groups else "val" if group in val_groups else None
            if split is None: outside_scope.append(name); continue
            data = z.read(image)
            with Image.open(io.BytesIO(data)) as decoded:
                if decoded.width*decoded.height>40000000 or getattr(decoded,"n_frames",1)!=1:
                    raise ValueError("图片超过限制")
                decoded.verify()
            sha = hashlib.sha256(data).hexdigest()
            if sha in used_hashes and used_hashes[sha] != split: raise ValueError("相同SHA图像跨集合")
            used_hashes[sha] = split
            pairs.append({"name":name,"source_group":group,"original_file_name":original,"split":split,
                          "image_sha256":sha,"lines":lines,"bytes":data})
    counts = {s:Counter() for s in ("train","val")}
    image_counts = {s:Counter() for s in ("train","val")}
    for pair in pairs:
        for cid,box in pair["lines"]: counts[pair["split"]][cid]+=1
        for cid in {v[0] for v in pair["lines"]}: image_counts[pair["split"]][cid]+=1
    gaps = [f"{s}:{names[c]}" for s in counts for c in range(len(names)) if not counts[s][c]]
    output.mkdir(parents=True,exist_ok=False)
    for s in ("train","val"):
        (output/"images"/s).mkdir(parents=True)
        (output/"labels"/s).mkdir(parents=True)
    source_assets=[]
    for pair in pairs:
        split,name = pair["split"],pair["name"]
        (output/"images"/split/name).write_bytes(pair["bytes"])
        label = output/"labels"/split/Path(name).with_suffix(".txt")
        text = "\n".join(str(cid)+" "+" ".join(format(v,".12g") for v in box) for cid,box in pair["lines"])+"\n"
        label.write_text(text,encoding="utf8")
        source_assets.append({k:v for k,v in pair.items() if k not in {"bytes","lines"}} | {"label_sha256":fingerprint(label)})
    version = "CV1-"+digest[:16]+"-"+mode+"-"+hashlib.sha256(json.dumps(source_assets,sort_keys=True).encode()).hexdigest()[:12]
    metadata={"dataset_version_id":version,"source":SOURCE_DOI,"source_archive_sha256":digest,
              "annotation_origin":"publisher_released; not reviewed in StepEye",
              "split_policy":"original R-prefix held out; physical independence not factory-verified",
              "source_assets":source_assets,"reviewed_negatives":[],
              "licenses":[{"component":"dataset","license":"CC-BY-4.0","source":SOURCE_DOI,
                           "commercial_use":False,"review_status":"attribution known; release approval not performed"}]}
    (output/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf8")
    (output/"classes.txt").write_text("\n".join(names)+"\n",encoding="utf8")
    (output/"dataset.yaml").write_text(yaml.safe_dump({"path":".","train":"images/train","val":"images/val",
                                                       "nc":len(names),"names":names}),encoding="utf8")
    report={"status":"blocked_missing_class" if gaps else "ready_for_experimental_training",
            "production_ready":False,"mode":mode,"classes":names,"dataset_version":version,
            "archive_sha256":digest,"train_groups":sorted(train_groups),"val_groups":sorted(val_groups),
            "images":{s:sum(p["split"]==s for p in pairs) for s in counts},
            "boxes":{s:dict(v) for s,v in counts.items()},"class_images":{s:dict(v) for s,v in image_counts.items()},
            "missing_class_splits":gaps,"excluded_missing_labels":len(missing),"excluded_outside_target":len(outside_scope),
            "local_human_reviewed":False,"warnings":["原始批次仅R1/R7；无法组成独立train/val/test三批次",
              "小验证集与原发布标签仅用于实验；不代表生产合格或默认500图/类预检通过"]}
    (output/"preparation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive",type=Path);parser.add_argument("output",type=Path)
    parser.add_argument("--mode",choices=["two-class","pinhole"],default="two-class")
    parser.add_argument("--train-groups",nargs="+",default=["R1"]);parser.add_argument("--val-groups",nargs="+",default=["R7"])
    args=parser.parse_args()
    try:
        result=prepare(args.archive,args.output,mode=args.mode,train_groups=args.train_groups,val_groups=args.val_groups)
        print(json.dumps(result,ensure_ascii=True));return 2 if result["missing_class_splits"] else 0
    except (ValueError,OSError,KeyError,zipfile.BadZipFile) as exc:
        print(json.dumps({"status":"failed","reason":str(exc)},ensure_ascii=True));return 2


if __name__=="__main__": raise SystemExit(main())

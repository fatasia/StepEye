"""数据集导入/打包(V12):X-AnyLabeling JSON 或 YOLO 目录 → TRAINING.md 规范数据集。

X-AnyLabeling 导出 JSON(每图一个同名 .json,shapes=[{label, points}])→ 转 YOLO txt;
YOLO 目录(images/+labels/+classes.txt)→ 校验配对与类别连续性 → 打包为可微调数据集
(含 dataset.yaml,D-FINE/RF-DETR/YOLO 系通用)。
"""
from __future__ import annotations

import json
import hashlib
import math
import shutil
from pathlib import Path
from typing import Any

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def _yolo_line(points: list[list[float]], img_w: int, img_h: int, cls_id: int) -> str:
    """多边形点 → YOLO 检测框(cx cy w h 归一化;取外接矩形)。"""
    if img_w <= 0 or img_h <= 0 or any(len(p) != 2 or not all(math.isfinite(float(v)) for v in p) for p in points):
        raise ValueError("非法图像尺寸或标注坐标")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if not (0 <= x0 < x1 <= img_w and 0 <= y0 < y1 <= img_h):
        raise ValueError("标注框超出图像或面积为零")
    cx, cy = (x0 + x1) / 2 / img_w, (y0 + y1) / 2 / img_h
    w, h = (x1 - x0) / img_w, (y1 - y0) / img_h
    return f"{cls_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def import_xanylabeling(json_path: Path, classes: list[str]) -> tuple[list[str], list[str]]:
    """单个 X-AnyLabeling JSON → (图片相对路径, YOLO txt 行列表);未知类别报错。"""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    img_path = str(data.get("imagePath", ""))
    w, h = int(data.get("imageWidth", 0)), int(data.get("imageHeight", 0))
    if not img_path or not w or not h:
        raise ValueError(f"{json_path.name}: 缺 imagePath/imageWidth/imageHeight")
    lines = []
    for shape in data.get("shapes", []):
        label = str(shape.get("label", ""))
        if label not in classes:
            raise ValueError(f"{json_path.name}: 类别「{label}」不在 classes 里({classes})")
        points = shape.get("points", [])
        if len(points) < 2:
            continue
        lines.append(_yolo_line(points, w, h, classes.index(label)))
    return img_path, lines


def build_dataset(src: Path, out: Path, classes: list[str], val_ratio: float = 0.15,
                  copy_mode: str = "copy") -> dict[str, Any]:
    """从源目录构建 TRAINING.md 规范数据集。

    src 两种形态自动识别:
    - 含 *.json(X-AnyLabeling 导出):逐个转换;
    - 含 images/+labels/(已是 YOLO):校验配对后原样整理。
    返回统计 {train_n, val_n, classes, problems}。
    """
    src = src.resolve()
    if not classes or len(set(classes)) != len(classes) or any(not c.strip() for c in classes):
        raise ValueError("类别不能为空或重复")
    if not math.isfinite(val_ratio) or not 0 < val_ratio < 1:
        raise ValueError("val_ratio 必须在0和1之间")
    if copy_mode != "copy":
        raise ValueError("导入只复制，保留原始数据；不支持 move")
    out = out / "dataset"
    if out.exists():
        raise ValueError("输出数据集已存在，请选择新目录")
    pairs: list[tuple[Path, list[str]]] = []  # (图片绝对路径, yolo 行)
    jsons = list(src.glob("*.json"))
    if jsons:
        for jp in jsons:
            img_rel, lines = import_xanylabeling(jp, classes)
            candidate = (src / img_rel).resolve()
            if not candidate.is_relative_to(src):
                raise ValueError("imagePath 必须位于导入目录内")
            candidates = [candidate, src / Path(img_rel).name]
            p = Path(img_rel)
            if not p.suffix:  # 部分导出无扩展名:按 stem 探测
                candidates += [src / f"{p.name}{ext}" for ext in sorted(IMG_EXTS)]
            img_abs = next((c for c in candidates if c.is_file()), None)
            if img_abs is not None and not img_abs.resolve().is_relative_to(src):
                raise ValueError("图片链接超出导入目录")
            if img_abs is None:
                raise FileNotFoundError(f"{jp.name}: 图片不存在 {img_rel}")
            pairs.append((img_abs, lines))
    elif (src / "images").is_dir():
        for img in sorted((src / "images").rglob("*")):
            if img.suffix.lower() not in IMG_EXTS:
                continue
            if not img.resolve().is_relative_to(src):
                raise ValueError("图片链接超出导入目录")
            lbl = src / "labels" / img.relative_to(src / "images").with_suffix(".txt")
            if not lbl.resolve().is_relative_to(src):
                raise ValueError("标签链接超出导入目录")
            lines = lbl.read_text(encoding="utf-8").splitlines() if lbl.is_file() else []
            for line in lines:
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) != 5 or not parts[0].isdigit() or not 0 <= int(parts[0]) < len(classes):
                    raise ValueError(f"{lbl.name}: 非法YOLO检测标签")
                x, y, w, h = map(float, parts[1:])
                if not all(math.isfinite(v) for v in (x,y,w,h)) or not (w > 0 and h > 0 and 0 <= x-w/2 <= x+w/2 <= 1 and 0 <= y-h/2 <= y+h/2 <= 1):
                    raise ValueError(f"{lbl.name}: 非法归一化框")
            pairs.append((img, lines if lbl.is_file() else None))
    else:
        raise ValueError(f"{src}: 未找到 X-AnyLabeling json 或 images/ 目录")

    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    (out / "classes.txt").write_text("\n".join(classes), encoding="utf-8")

    problems: list[str] = []
    n_train = n_val = 0
    val_cut = max(1, int(len(pairs) * val_ratio))
    for i, (img, lines) in enumerate(pairs):
        split = "val" if i < val_cut else "train"
        unique = img.stem + "-" + hashlib.sha256(img.relative_to(src).as_posix().encode()).hexdigest()[:12]
        dst_img = out / "images" / split / f"{unique}{img.suffix.lower()}"
        if copy_mode == "move":
            shutil.move(str(img), dst_img)
        else:
            shutil.copy2(img, dst_img)
        if lines is not None:
            (out / "labels" / split / f"{unique}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        if not lines:
            problems.append(f"{img.name}: 无标注或空标签：需人工复核，不能直接当负样本")
        if split == "val":
            n_val += 1
        else:
            n_train += 1
    (out / "dataset.yaml").write_text(
        f"path: {out.resolve().as_posix()}\ntrain: images/train\nval: images/val\n"
        f"nc: {len(classes)}\nnames: {json.dumps(classes, ensure_ascii=False)}\n", encoding="utf-8")
    return {"train_n": n_train, "val_n": n_val, "classes": classes, "problems": problems,
            "dataset_yaml": str(out / "dataset.yaml")}

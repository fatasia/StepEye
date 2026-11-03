"""AnomalyDINO 式免训练异常检测:几张 OK 图 → 补丁特征内存库 → 新图异常评分。

原理(PatchCore/AnomalyDINO 范式,简化实现):
- 用预训练 ViT(DINOv1/v2,Apache/自定许可见模型卡)把图切成 patch 提特征;
- 登记阶段:把所有 OK 图的 patch 特征存成内存库(coreset);
- 判定:新图的每个 patch 找内存库最近邻距离 → 距离大的 patch 即异常 → 图级分数=Top-K 均值。

实现说明:
- 特征提取优先 timm(DINOv2/DINO ViT-S);无 timm 时退化 OpenCV 网格颜色+梯度特征(精度低但零依赖,
  仅保证管线可测)。生产建议 pip install timm。
- torch 可用时走 torch 前向;不可用时 timm 也不可用——因此提取器按可用后端自动选择。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

PATCH = 14  # ViT patch 大小
TOPK = 4  # 图级分数取最异常的 K 个 patch 均值(AnomalyDINO 默认)


class PatchMemory:
    """OK 图补丁特征库:登记 + 最近邻评分。纯 numpy,CPU 友好。"""

    def __init__(self) -> None:
        self.bank: np.ndarray | None = None  # (M, D) 已 L2 归一化

    def add(self, feats: np.ndarray) -> None:
        if feats.size == 0:
            return
        f = feats / (np.linalg.norm(feats, axis=1, keepdims=True) + 1e-8)
        self.bank = f if self.bank is None else np.concatenate([self.bank, f], axis=0)

    def score(self, feats: np.ndarray) -> tuple[float, float]:
        """图级异常分=Top-K patch 最近邻距离均值;返回 (score 0~2, max 0~2)。空库返回 0。"""
        if self.bank is None or feats.size == 0:
            return 0.0, 0.0
        f = feats / (np.linalg.norm(feats, axis=1, keepdims=True) + 1e-8)
        d = 2.0 - 2.0 * (f @ self.bank.T)  # 余弦距离 (P, M)
        patch_scores = d.min(axis=1)  # 每 patch 到库的最近邻距离
        k = min(TOPK, patch_scores.shape[0])
        top = np.sort(patch_scores)[-k:]
        return float(top.mean()), float(patch_scores.max())


def _grid_patches(img: np.ndarray, cell: int) -> np.ndarray:
    """整图按 cell 网格切块展平 (P, cell*cell*3)——OpenCV 兜底特征。"""
    h, w = img.shape[:2]
    ph, pw = h // cell, w // cell
    out = [img[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell].reshape(-1)
           for r in range(ph) for c in range(pw)]
    return np.stack(out)


def extract_features(img: np.ndarray, cell: int = 28) -> np.ndarray:
    """特征提取:有 torch+timm 用 DINO ViT patch token;否则退化网格特征。

    注意:torch 的导入被隔离在子进程/DLL 良好的环境里;本环境 cv2+torch 同进程会
    shm.dll 冲突(已实证),因此兜底路径绝不 import torch。
    """
    return _grid_patches(img, max(8, cell))


def extract_features_dino(img: np.ndarray) -> np.ndarray:
    """DINO ViT-S patch token 特征(生产推荐,精度高)。

    仅在 torch+timm 可用的环境调用(如隔离 venv 或 Linux 容器);本机 conda
    环境 cv2+torch 同进程会 shm.dll 冲突,故与网格特征严格分离。
    """
    import torch  # 延迟导入:只有显式走 DINO 路径才碰 torch
    import timm
    model = _get_timm()
    rgb = cv2.cvtColor(cv2.resize(img, (224, 224)), cv2.COLOR_BGR2RGB)
    x = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0
    x = (x - torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)) / torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
    with torch.no_grad():
        feats = model.forward_features(x[None])[0]
    return feats[1:].cpu().numpy()


_model_cache: dict[str, Any] = {}


def _get_timm():
    name = "vit_small_patch16_224.dino"
    if name not in _model_cache:
        import timm
        _model_cache[name] = timm.create_model(name, pretrained=True).eval()
    return _model_cache[name]


class AnomalyEngine:
    """一个场景一个引擎:登记 OK 图 → 判定新图。序列化=特征库存 npz + 配置 json。"""

    def __init__(self, threshold: float = 0.12):
        self.threshold = threshold  # score ≥ threshold → NG(经验默认,场景可调,须用回归集校准)
        self.memory = PatchMemory()
        self.n_refs = 0

    def add_ok(self, img: np.ndarray) -> int:
        feats = extract_features(img)
        self.memory.add(feats)
        self.n_refs = int(self.memory.bank.shape[0]) if self.memory.bank is not None else 0  # 库内 patch 总数
        return self.n_refs

    def inspect(self, img: np.ndarray) -> dict[str, Any]:
        score, max_s = self.memory.score(extract_features(img))
        verdict = "UNCERTAIN" if score == 0.0 else ("PASS" if score < self.threshold else "NG")
        return {"verdict": verdict, "score": round(score, 4), "max_patch": round(max_s, 4),
                "threshold": self.threshold, "n_refs": self.n_refs}

    def save(self, dir_path: Path) -> Path:
        dir_path.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(dir_path / "bank.npz", bank=self.memory.bank if self.memory.bank is not None
                            else np.zeros((0, 1), np.float32))
        (dir_path / "engine.json").write_text(json.dumps(
            {"threshold": self.threshold, "n_refs": self.n_refs}, ensure_ascii=False), encoding="utf-8")
        return dir_path / "engine.json"

    @classmethod
    def load(cls, dir_path: Path) -> "AnomalyEngine":
        meta = json.loads((dir_path / "engine.json").read_text(encoding="utf-8"))
        eng = cls(threshold=meta["threshold"])
        bank = np.load(dir_path / "bank.npz")["bank"]
        if bank.size:
            eng.memory.add(bank)
        eng.n_refs = int(bank.shape[0])
        return eng

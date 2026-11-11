"""良品特征库：网格基线或用户选择的 DINOv2 ONNX patch 特征。"""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from .exposure import normalize_exposure

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

    def distances(self, feats: np.ndarray) -> np.ndarray:
        if self.bank is None or feats.size == 0:
            return np.zeros(len(feats), np.float32)
        f = feats / (np.linalg.norm(feats, axis=1, keepdims=True) + 1e-8)
        # 分块检索，避免样本库增长时一次分配 P×M 距离矩阵。
        return np.concatenate([np.clip(2 - 2 * (block @ self.bank.T), 0, 4).min(axis=1)
                               for block in np.array_split(f, max(1, math.ceil(len(f)/128)))])

    def score(self, feats: np.ndarray) -> tuple[float, float]:
        """图级异常分=Top-K patch 最近邻距离均值;返回 (score 0~2, max 0~2)。空库返回 0。"""
        if self.bank is None or feats.size == 0:
            return 0.0, 0.0
        patch_scores = self.distances(feats)
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
    """网格像素基线，不依赖深度模型。"""
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


def dinov2_path() -> Path:
    """管理员指定本机 ViT-S/14 ONNX；不从请求接受任意路径。"""
    path = os.environ.get("STEPEYE_DINOV2_ONNX", str(Path(__file__).resolve().parent.parent / "models" / "dinov2-vits14-patches.onnx"))
    if not path or not Path(path).is_file():
        raise ValueError("DINOv2 模型未配置，请设置 STEPEYE_DINOV2_ONNX")
    return Path(path).resolve()


def dinov2_fingerprint() -> str:
    digest = hashlib.sha256()
    with dinov2_path().open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_features_dinov2(img: np.ndarray, expected_sha: str) -> np.ndarray:
    """固定契约：RGB 224×224，输出 [1,256,384] 的 ViT-S/14 patch tokens。"""
    actual = dinov2_fingerprint()
    if actual != expected_sha:
        raise ValueError("DINOv2 模型文件已变化，请恢复原模型或新建场景")
    try:
        import onnxruntime as ort
    except ImportError as e:
        raise ValueError("使用 DINOv2 需要安装 onnxruntime") from e
    key = (str(dinov2_path()), actual)
    if key not in _model_cache:
        options=ort.SessionOptions()
        options.intra_op_num_threads=2
        options.inter_op_num_threads=1
        options.add_session_config_entry('session.intra_op.allow_spinning','0')
        try:
            session = ort.InferenceSession(str(dinov2_path()), sess_options=options, providers=["CPUExecutionProvider"])
        except Exception as exc:
            raise ValueError('DINOv2 模型无法加载，请检查本地模型文件') from exc
        if len(session.get_inputs()) != 1 or len(session.get_outputs()) != 1:
            raise ValueError("DINOv2 ONNX 必须有一个输入和一个 patch tokens 输出")
        _model_cache.clear()
        _model_cache[key] = session
    session = _model_cache[key]
    rgb = cv2.cvtColor(cv2.resize(img, (224, 224)), cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    rgb = (rgb - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])
    try:
        result = np.asarray(session.run(None, {session.get_inputs()[0].name: tensor})[0])
    except Exception as exc:
        raise ValueError('DINOv2 推理失败，请检查模型输入契约') from exc
    if result.shape != (1, 256, 384) or not np.isfinite(result).all():
        raise ValueError("DINOv2 ONNX 输出须为有限的 [1,256,384] patch tokens")
    return result[0].astype(np.float32)


def _get_timm():
    name = "vit_small_patch16_224.dino"
    if name not in _model_cache:
        import timm
        _model_cache[name] = timm.create_model(name, pretrained=True).eval()
    return _model_cache[name]


class AnomalyEngine:
    """一个场景一个引擎:登记 OK 图 → 判定新图。序列化=特征库存 npz + 配置 json。"""

    def __init__(self, threshold: float = 0.12, backend: str = "grid", model_sha: str | None = None,
                 lighting: str = 'off'):
        if backend not in ("grid", "dinov2"):
            raise ValueError("未知特征后端")
        if lighting not in ('off', 'auto'):
            raise ValueError('未知光照处理方式')
        self.threshold = threshold  # score ≥ threshold → NG(经验默认,场景可调,须用回归集校准)
        self.backend = backend
        self.lighting = lighting
        self.model_sha = model_sha if backend == "dinov2" else None
        if backend == "dinov2" and self.model_sha is None:
            self.model_sha = dinov2_fingerprint()
        self.memory = PatchMemory()
        self.n_refs = 0

    def _features(self, img: np.ndarray) -> np.ndarray:
        if self.lighting == 'auto':
            img, _ = normalize_exposure(img)
        return extract_features(img) if self.backend == "grid" else extract_features_dinov2(img, self.model_sha)

    def add_ok(self, img: np.ndarray) -> int:
        feats = self._features(img)
        self.memory.add(feats)
        self.n_refs = int(self.memory.bank.shape[0]) if self.memory.bank is not None else 0  # 库内 patch 总数
        return self.n_refs

    def inspect(self, img: np.ndarray) -> dict[str, Any]:
        features = self._features(img)
        distances = self.memory.distances(features)
        score, max_s = float(np.sort(distances)[-min(TOPK,len(distances)):].mean()), float(distances.max())
        exposure = normalize_exposure(img)[1] if self.lighting == 'auto' else None
        verdict = "UNCERTAIN" if self.memory.bank is None or (exposure and exposure['unusable']) else ("PASS" if score < self.threshold else "NG")
        shape = [16,16] if self.backend=='dinov2' else [img.shape[0]//28, img.shape[1]//28]
        return {"verdict": verdict, "score": round(score, 4), "max_patch": round(max_s, 4),
                "threshold": self.threshold, "n_refs": self.n_refs, "backend": self.backend,
                "heatmap": distances.reshape(shape).round(4).tolist(), "lighting": self.lighting,
                "exposure": exposure}

    def save(self, dir_path: Path) -> Path:
        dir_path.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(dir_path / "bank.npz", bank=self.memory.bank if self.memory.bank is not None
                            else np.zeros((0, 1), np.float32))
        (dir_path / "engine.json").write_text(json.dumps(
            {"threshold": self.threshold, "n_refs": self.n_refs, "backend": self.backend,
             "model_sha": self.model_sha, "lighting": self.lighting}, ensure_ascii=False), encoding="utf-8")
        return dir_path / "engine.json"

    @classmethod
    def load(cls, dir_path: Path) -> "AnomalyEngine":
        meta = json.loads((dir_path / "engine.json").read_text(encoding="utf-8"))
        eng = cls(threshold=meta["threshold"], backend=meta.get("backend", "grid"), model_sha=meta.get("model_sha"), lighting=meta.get('lighting', 'off'))
        with np.load(dir_path / "bank.npz", allow_pickle=False) as data:
            bank = data["bank"]
        if bank.size:
            eng.memory.add(bank)
        eng.n_refs = int(bank.shape[0])
        return eng

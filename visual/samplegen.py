"""样本生成:上传参考图 + 描述 → 批量产出带标注的样本(增强合成 / 生成式扩充)。

两个引擎,标注语义不同,manifest 里如实标注:
- augment(默认,离线秒级):对参考图做保持标签的变换(光照扫掠/噪声/模糊/旋转/缩放)。
  标注直接继承参考图标签;变换参数写入 manifest 供下游过滤。光照扫掠直接产出
  多光照评测子集(ROADMAP-v2 光照治理的落地件)。
- cogview(生成式,需 API,秒级/张):VLM 读参考图写出场景提示词 → CogView 按描述
  批量生成新场景图。标注继承自用户给的标签,但生成图与真实产线有差距——
  只用于冷启动/预览/提示词调试,不得直接当验收样本(诚实红线)。
"""
from __future__ import annotations

import base64
import json
import random
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .llm_provider import ProviderConfig, VLMProvider

# 生成式引擎的诚实上限:一次批量别超过这个数(每张 5~15s,且保真度有限)
COGVIEW_MAX_BATCH = 8  # 生成式单批上限:保真度有限


def augment_one(img: np.ndarray, rng: random.Random) -> tuple[np.ndarray, dict[str, Any]]:
    """对参考图做一组保持标签的随机变换;参数回传供 manifest 记录。"""
    out = img.copy()
    params: dict[str, Any] = {}
    gamma = rng.uniform(0.6, 1.6)  # 光照扫掠:gamma 越小越暗
    lut = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)]).clip(0, 255).astype("uint8")
    out = cv2.LUT(out, lut)
    params["gamma"] = round(gamma, 3)
    beta = rng.uniform(-0.25, 0.25)  # 亮度偏移
    out = np.clip(out.astype(np.float32) * (1 + beta), 0, 255).astype(np.uint8)
    params["brightness_shift"] = round(beta, 3)
    contrast = rng.uniform(0.85, 1.15)
    out = np.clip((out.astype(np.float32) - 128) * contrast + 128, 0, 255).astype(np.uint8)
    params["contrast"] = round(contrast, 3)
    sigma = rng.uniform(0, 6)
    if sigma >= 0.5:
        noise = np.array(cv2.randn(np.zeros(out.shape, np.float32), 0, sigma))
        out = np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        params["noise_sigma"] = round(sigma, 2)
    k = rng.choice([0, 0, 1, 3])  # 轻度模糊;0=不模糊
    if k:
        out = cv2.GaussianBlur(out, (k * 2 + 1, k * 2 + 1), 0)
        params["blur_px"] = k
    angle = rng.uniform(-8, 8)
    if abs(angle) >= 1:
        h, w = out.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        out = cv2.warpAffine(out, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
        params["rotate_deg"] = round(angle, 2)
    scale = rng.uniform(0.92, 1.08)
    if abs(scale - 1) >= 0.02:
        h, w = out.shape[:2]
        out = cv2.resize(out, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        ch, cw = out.shape[:2]
        canvas = np.zeros_like(img)
        x0, y0 = max(0, (img.shape[1] - cw) // 2), max(0, (img.shape[0] - ch) // 2)
        canvas[y0:y0 + min(ch, img.shape[0]), x0:x0 + min(cw, img.shape[1])] = out[:min(ch, img.shape[0]), :min(cw, img.shape[1])]
        out = canvas if cw < img.shape[1] or ch < img.shape[0] else cv2.resize(out, (w, h))
        params["scale"] = round(scale, 3)
    return out, params


def augment_batch(ref_path: Path, label: str, n: int, out_dir: Path, seed: int = 7) -> list[dict[str, Any]]:
    img = cv2.imdecode(np.fromfile(str(ref_path), dtype=np.uint8), cv2.IMREAD_COLOR)  # 中文路径安全
    if img is None:
        raise ValueError(f"参考图无法解码: {ref_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    stem = ref_path.stem
    rows = []
    for i in range(n):
        variant, params = augment_one(img, rng)
        name = f"{stem}_{label}_{i:03d}.jpg"
        cv2.imwrite(str(out_dir / name), variant, [cv2.IMWRITE_JPEG_QUALITY, 90])
        rows.append({"file": name, "label": label, "engine": "augment", "source": ref_path.name,
                     "params": params})
    return rows


GEN_PROMPT = (
    "看这张工厂现场照片,写一段图像生成提示词,生成与它同类场景的新样本。要求:"
    "保留场景类型/相机视角/工件类型,描述里必须体现:{description}。"
    "只输出生成提示词本身(中文,不超过80字),不要解释。"
)

VARIATION_HINTS = ["画面视角略微不同", "光照偏暗一些", "光照偏亮一些", "构图稍偏", "画面元素位置略有变化",
                   "背景细节不同", "色调略有偏差", "轻微运动模糊感"]


def _images_generate(cfg: ProviderConfig, model: str, prompt: str, size: str,
                     transport=None, fetcher=None) -> bytes:
    """OpenAI 兼容 images/generations 生成(CogView / lk888 tt-image-2.5 等网关通用)。"""
    post = transport.post if transport is not None else _default_post
    fetch = fetcher or (lambda url, timeout: urllib.request.urlopen(url, timeout=timeout))
    base = cfg.base_url.rstrip("/")
    if "/chat/completions" in base:  # chat 端点(如智谱 paas/v4):去掉尾段换 images
        base = base[: base.rfind("/chat/completions")]
    resp = post(base + "/images/generations",
                {"Authorization": f"Bearer {cfg.api_key}"},
                {"model": model, "prompt": prompt, "size": size})
    d0 = resp["data"][0]
    if d0.get("b64_json"):
        import base64
        return base64.b64decode(d0["b64_json"])
    with fetch(d0["url"], 120) as r:  # url 来自配置的官方端点返回体
        return r.read()


def cogview_generate(cfg: ProviderConfig, prompt: str, size: str = "1024x1024",
                     transport=None, fetcher=None) -> bytes:
    """CogView-3-Flash 生成(智谱官方端点)。"""
    return _images_generate(cfg, "cogview-3-flash", prompt, size, transport, fetcher)


def lk888_generate(cfg: ProviderConfig, prompt: str, size: str = "1024x1024",
                   transport=None, fetcher=None) -> bytes:
    """lk888 网关 tt-image-2.5 生成:凭据取 GEN_API_KEY/GEN_BASE/GEN_MODEL(.env 可配)。"""
    from .llm_provider import load_env_file, _default_env_path
    file_vals = load_env_file(_default_env_path())
    cfg2 = ProviderConfig(
        base_url=(os.environ.get("GEN_BASE") or file_vals.get("GEN_BASE") or "https://api.lk888.ai").rstrip("/"),
        api_key=os.environ.get("GEN_API_KEY") or file_vals.get("GEN_API_KEY") or cfg.api_key,
        model=os.environ.get("GEN_MODEL") or file_vals.get("GEN_MODEL") or "tt-image-2.5")
    path = "/v1/images/generations"  # OpenAI 兼容网关固定路径
    post = transport.post if transport is not None else _default_post
    fetch = fetcher or (lambda url, timeout: urllib.request.urlopen(url, timeout=timeout))
    resp = post(cfg2.base_url + path,
                {"Authorization": f"Bearer {cfg2.api_key}"},
                {"model": cfg2.model, "prompt": prompt, "size": size})
    d0 = resp["data"][0]
    if d0.get("b64_json"):
        import base64
        return base64.b64decode(d0["b64_json"])
    with fetch(d0["url"], 120) as r:
        return r.read()


def _default_post(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
        return json.load(r)


def generate_batch(ref_path: Path, label: str, description: str, n: int, out_dir: Path,
                   engine: str = "augment", seed: int = 7,
                   vlm: VLMProvider | None = None, cfg: ProviderConfig | None = None,
                   cogview_transport=None, cogview_fetcher=None) -> list[dict[str, Any]]:
    """统一入口;augment 离线,cogview 走「VLM 读图→写提示词→生成」。返回 manifest 行(含失败行)。"""
    if engine == "configured":
        if not 1 <= n <= COGVIEW_MAX_BATCH:
            raise ValueError("configured generation batch must be 1..8")
        from .configured_image import configured_batch
        return configured_batch(ref_path, label, description, n, out_dir, cfg)
    if engine == "augment":
        return augment_batch(ref_path, label, n, out_dir, seed)
    if engine not in ("cogview", "lk888"):
        raise ValueError(f"未知生成引擎: {engine}(可选 cogview / lk888)")
    if n > COGVIEW_MAX_BATCH:
        raise ValueError(f"生成式一次最多 {COGVIEW_MAX_BATCH} 张(保真度有限,别当验收样本)")
    cfg = cfg or ProviderConfig.from_env()
    if not cfg.api_key:
        raise RuntimeError("缺少 ZHIPU_API_KEY")
    vlm = vlm or VLMProvider(cfg)
    ref_b64 = base64.b64encode(ref_path.read_bytes()).decode()
    # 写提示词这步是自然语言,不需要 JSON——直接 chat 取全文
    base_prompt = vlm.chat(vlm.image_message([ref_b64],
                           GEN_PROMPT.replace("{description}", description))).strip().strip('"')
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    stem, rows = ref_path.stem, []
    for i in range(n):
        prompt = f"{base_prompt},{VARIATION_HINTS[i % len(VARIATION_HINTS)]}"
        try:
            gen = cogview_generate if engine == "cogview" else lk888_generate
            data = gen(cfg, prompt, transport=cogview_transport, fetcher=cogview_fetcher)
            name = f"{stem}_{label}_gen{i:03d}.jpg"
            (out_dir / name).write_bytes(data)
            rows.append({"file": name, "label": label, "engine": engine, "source": ref_path.name,
                         "prompt": prompt, "note": "生成式样本:与真实产线有差距,仅冷启动/调试用"})
        except Exception as err:  # noqa: BLE001 —— 单张失败计入 manifest,不中断批量
            rows.append({"file": None, "label": label, "engine": engine, "error": str(err)[:200]})
    return rows


def write_manifest(out_dir: Path, rows: list[dict[str, Any]]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    mp = out_dir / "samples.manifest.jsonl"
    with mp.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"created": datetime.now().isoformat(timespec="seconds"), **r},
                               ensure_ascii=False) + "\n")
    return mp

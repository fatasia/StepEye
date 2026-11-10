"""V0 冒烟:实测 glm-5.3-flash(coding 端点)的文本 / 视觉 / JSON 稳定性。

用法: python tools/probe_vlm.py <图片路径> [重复次数]
凭据从环境变量或 sop-vision/.env 读取;结果 JSON 写到图片同目录 probe_result.json。
不打印 API key。这是 ADR-002 的验证证据,结果回填 docs/V0-SMOKE.md。
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from visual.llm_provider import ProviderConfig, VLMProvider  # noqa: E402

GLOVE_PROMPT = (
    '判断画面中作业人员是否佩戴手套。只输出JSON:'
    '{"verdict":"PASS|NG|UNCERTAIN","confidence":0到1的小数,"reason":"不超过20字"}'
)


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python tools/probe_vlm.py <图片路径> [重复次数]")
        return 2
    img = Path(sys.argv[1])
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    cfg = ProviderConfig.from_env()
    if not cfg.api_key:
        print("缺少 ZHIPU_API_KEY(环境变量或 sop-vision/.env)")
        return 2
    print(f"端点 {cfg.base_url}  模型 {cfg.model}(key 不回显)")
    provider = VLMProvider(cfg)
    b64 = base64.b64encode(img.read_bytes()).decode()
    result: dict = {"base_url": cfg.base_url, "model": cfg.model, "image": img.name, "tests": {}}

    # T1 文本连通性
    try:
        reply = provider.chat([{"role": "user", "content": "只回复两个字:可用"}])
        result["tests"]["text"] = {"ok": bool(reply.strip()), "reply": reply[:50]}
    except Exception as err:  # noqa: BLE001 —— 冒烟脚本要的是失败原因,不是漂亮堆栈
        result["tests"]["text"] = {"ok": False, "error": str(err)[:300]}

    # T2 视觉:coding 端点是否放行 image_url(ADR-002 的关键未知项)
    try:
        reply = provider.chat(provider.image_message(
            [b64], "用不超过60字描述画面中的人员动作、手部位置与桌面物品。"))
        result["tests"]["vision"] = {"ok": True, "reply": reply[:300]}
    except Exception as err:  # noqa: BLE001
        result["tests"]["vision"] = {"ok": False, "error": str(err)[:300]}

    # T3 结构化 JSON 判定稳定性:同一张图重复 n 次
    verdicts = []
    for i in range(runs):
        out = provider.inspect([b64], GLOVE_PROMPT)
        verdicts.append({"ok": out["ok"], "verdict": out["verdict"],
                         "confidence": out["confidence"], "reason": out["reason"][:40]})
        print(f"  T3 {i + 1}/{runs}: ok={out['ok']} verdict={out['verdict']} conf={out['confidence']}")
    ok_runs = [v for v in verdicts if v["ok"]]
    result["tests"]["json_stability"] = {
        "runs": runs,
        "parse_ok_rate": round(len(ok_runs) / runs, 2) if runs else None,
        "distinct_verdicts": sorted({str(v["verdict"]) for v in ok_runs}),
        "detail": verdicts,
    }

    dest = img.parent / "probe_result.json"
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["tests"], ensure_ascii=False, indent=2))
    print(f"\n结果已写入 {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

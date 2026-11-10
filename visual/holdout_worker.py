"""独立进程运行一次固定 SHA 留出评测；请求从私有文件读取，stdin 关闭，不接 HTTP。

完整报告由 run_holdout 直接写到输出文件；stdout 只输出小摘要，
错误详情写独立文件，父进程据此回传用户可读的失败原因。
"""
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 2
    from visual.detection_holdout import run_holdout
    try:
        report = run_holdout(spec["dataset"], spec["model"], spec["output"],
                             model_sha256=spec["model_sha256"], dataset_sha256=spec["dataset_sha256"],
                             adapter=spec["adapter"], confidence=spec["confidence"], iou=spec["iou"])
    except Exception as exc:  # 进程边界:任何失败都折算成退出码+受限长度的原因文本
        try:
            Path(spec["error"]).write_text(json.dumps({"detail": str(exc)[:500]}, ensure_ascii=False),
                                           encoding="utf-8")
        except OSError:
            pass
        return 1
    print(json.dumps({"frames": report["evaluation"]["frames"], "summary": report["evaluation"]["summary"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

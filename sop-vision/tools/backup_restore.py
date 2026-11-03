"""StepEye 数据目录备份/恢复 CLI。用法(在 sop-vision 目录运行):

    python -m tools.backup_restore backup <数据目录> <输出目录>
    python -m tools.backup_restore restore <备份目录> <恢复目标目录>
    python -m tools.backup_restore verify <备份目录>

备份:SQLite 库一致性快照(WAL 并入)+ 关键文件按清单复制,生成 manifest.json(含 sha256)。
恢复:先校验备份哈希,再恢复到全新目录(目标已存在则拒绝),恢复后逐文件复检。
校验:只读比对 manifest 哈希,不改动任何文件。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from visual.backup import BackupError, backup, restore, verify  # noqa: E402


def _dir_arg(value: str) -> Path:
    if not value.strip():
        raise argparse.ArgumentTypeError("目录路径不能为空")
    return Path(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backup_restore", description="StepEye 数据目录备份/恢复工具(不覆盖任何现有数据)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backup = sub.add_parser("backup", help="备份数据目录:库一致性快照 + 关键文件清单复制")
    p_backup.add_argument("data_dir", type=_dir_arg, help="StepEye 数据目录(只读,不会被改动)")
    p_backup.add_argument("out_dir", type=_dir_arg, help="备份输出目录(须为空或不存在)")

    p_restore = sub.add_parser("restore", help="校验备份后恢复到全新目录;目标已存在则拒绝")
    p_restore.add_argument("backup_dir", type=_dir_arg, help="备份目录(含 manifest.json)")
    p_restore.add_argument("target_dir", type=_dir_arg, help="恢复目标目录(必须不存在)")

    p_verify = sub.add_parser("verify", help="只读校验备份目录的文件完整性与哈希")
    p_verify.add_argument("backup_dir", type=_dir_arg, help="备份目录(含 manifest.json)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            manifest = backup(args.data_dir, args.out_dir)
            skipped = len(manifest["skipped"])
            print(f"备份完成:{len(manifest['files'])} 个文件 → {args.out_dir}"
                  f"(跳过 {skipped} 项,明细见 manifest.json)")
        elif args.command == "restore":
            result = restore(args.backup_dir, args.target_dir)
            print(f"恢复完成:{result['restored']} 个文件 → {result['target']}(哈希逐文件复检通过)")
        else:
            check = verify(args.backup_dir)
            if check["ok"]:
                print(f"校验通过:{check['checked']} 个文件哈希一致")
            else:
                print(f"校验失败:缺失 {check['missing']},哈希不符 {check['corrupt']}", file=sys.stderr)
                return 1
    except BackupError as e:
        print(f"失败:{e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

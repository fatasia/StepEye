"""标注数据层：原件保留、不可变修订、人工真值与分组版本导出。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import random
import sqlite3
import threading
from typing import Any
import uuid
import warnings
import zipfile

from PIL import Image, ImageOps, UnidentifiedImageError

from .storage_quota import quota_write

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
STATES = ("unlabeled", "draft", "suggested", "confirmed", "background")
SCHEMA_VERSION = 1


class LabelError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any, max_bytes: int = 64 * 1024) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise LabelError("字段必须是有限 JSON 值") from None
    if len(text.encode("utf-8")) > max_bytes:
        raise LabelError("JSON 字段超过大小限制", 413)
    return text


def _text(value: str, name: str, limit: int = 160, required: bool = False) -> str:
    if not isinstance(value, str):
        raise LabelError(f"{name}必须为文字")
    value = value.strip()
    if len(value) > limit or any(ord(c) < 32 for c in value) or (required and not value):
        raise LabelError(f"{name}为空、过长或包含控制字符")
    return value


def _targets(items, classes: list[str]) -> list[dict]:
    if not isinstance(items, list) or len(items) > 1000:
        raise LabelError("targets 必须为最多 1000 个框的列表")
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            raise LabelError("每个目标必须是对象")
        cid, xy = item.get("label_id"), item.get("xyxy_norm")
        if isinstance(cid, bool) or not isinstance(cid, int) or not 0 <= cid < len(classes):
            raise LabelError("目标类别不在项目类别表内")
        if not isinstance(xy, (list, tuple)) or len(xy) != 4:
            raise LabelError("xyxy_norm 需要四个归一化坐标")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in xy):
            raise LabelError("框坐标必须是有限数字")
        x0, y0, x1, y1 = map(float, xy)
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise LabelError("框必须非空、顺序正确且在 0..1 内")
        target = {"label_id": cid, "xyxy_norm": [x0, y0, x1, y1]}
        if "confidence" in item:
            conf = item["confidence"]
            if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not math.isfinite(conf) or not 0 <= conf <= 1:
                raise LabelError("置信度必须是 0..1 内的有限数字")
            target["confidence"] = float(conf)
        if "id" in item:
            target["id"] = _text(item["id"], "目标 id", 96, True)
        cleaned.append(target)
    return cleaned


class LabelStore:
    def __init__(self, root: Path, quota=None):
        self.quota = quota
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in ("originals", "images", "versions"):
            path = self.root / folder
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise LabelError("标注存储目录不能链接到其他目录")
            path.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        db_path = self.root / "labels.db"
        if db_path.is_symlink() or not db_path.resolve().is_relative_to(self.root):
            raise LabelError("标注数据库不能链接到其他文件")
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        if self._db.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
            self._db.close()
            raise LabelError("标注数据库版本较新，请升级服务", 409)
        self._db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS label_projects (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,classes TEXT NOT NULL,created_at TEXT NOT NULL,
              scene_id TEXT NOT NULL DEFAULT '',license_ref TEXT NOT NULL DEFAULT '',schema_revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS label_assets (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES label_projects(id),name TEXT NOT NULL,
              original_file TEXT NOT NULL,image_file TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,
              sha256 TEXT NOT NULL,image_sha256 TEXT NOT NULL DEFAULT '',group_id TEXT NOT NULL DEFAULT '',
              synthetic INTEGER NOT NULL DEFAULT 0,license_ref TEXT NOT NULL DEFAULT '',
              illumination_bucket TEXT NOT NULL DEFAULT 'unknown',metadata TEXT NOT NULL DEFAULT '{}',
              revision INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'unlabeled',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS label_revisions (
              asset_id TEXT NOT NULL REFERENCES label_assets(id),revision INTEGER NOT NULL,parent_revision INTEGER,
              producer TEXT NOT NULL,actor TEXT NOT NULL,state TEXT NOT NULL,targets TEXT NOT NULL,
              provenance TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,PRIMARY KEY(asset_id,revision));
            CREATE TABLE IF NOT EXISTS label_versions (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES label_projects(id),manifest TEXT NOT NULL,
              sha256 TEXT NOT NULL,zip_file TEXT NOT NULL,zip_sha256 TEXT NOT NULL,created_at TEXT NOT NULL,actor TEXT NOT NULL);
        """)
        # 原型 v0 的附加元数据采用增列迁移，不重建/清空已有项目或修订。
        additions = {
            "label_projects": {"scene_id": "TEXT NOT NULL DEFAULT ''", "license_ref": "TEXT NOT NULL DEFAULT ''",
                               "schema_revision": "INTEGER NOT NULL DEFAULT 1"},
            "label_assets": {"image_sha256": "TEXT NOT NULL DEFAULT ''", "group_id": "TEXT NOT NULL DEFAULT ''",
                             "synthetic": "INTEGER NOT NULL DEFAULT 0", "license_ref": "TEXT NOT NULL DEFAULT ''",
                             "illumination_bucket": "TEXT NOT NULL DEFAULT 'unknown'", "metadata": "TEXT NOT NULL DEFAULT '{}'"},
            "label_revisions": {"provenance": "TEXT NOT NULL DEFAULT '{}'"},
        }
        for table, fields in additions.items():
            current = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
            for name, spec in fields.items():
                if name not in current:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")
        self._db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_label_assets_project ON label_assets(project_id,created_at,id);
            CREATE INDEX IF NOT EXISTS idx_label_versions_project ON label_versions(project_id,created_at);
            CREATE TRIGGER IF NOT EXISTS label_revision_no_update BEFORE UPDATE ON label_revisions
              BEGIN SELECT RAISE(ABORT,'annotation revisions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS label_revision_no_delete BEFORE DELETE ON label_revisions
              BEGIN SELECT RAISE(ABORT,'annotation revisions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS label_version_no_update BEFORE UPDATE ON label_versions
              BEGIN SELECT RAISE(ABORT,'dataset versions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS label_version_no_delete BEFORE DELETE ON label_versions
              BEGIN SELECT RAISE(ABORT,'dataset versions are immutable'); END;
        """)
        self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _write(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def _file(self, relative: str, exists: bool = True) -> Path:
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise LabelError("非法标注资源路径")
        if exists and not path.is_file():
            raise LabelError("标注资源文件不存在", 404)
        return path

    @staticmethod
    def _project(row) -> dict:
        out = dict(row)
        out["classes"] = json.loads(out["classes"])
        return out

    def create_project(self, name, classes, scene_id: str = "", license_ref: str = "") -> dict:
        name = _text(name, "项目名称", 120, True)
        if not isinstance(classes, list) or not 1 <= len(classes) <= 512:
            raise LabelError("类别表需要 1..512 个类别")
        classes = [_text(c, "类别名称", 80, True) for c in classes]
        if len(set(classes)) != len(classes):
            raise LabelError("类别名称不能重复")
        pid = "LP-" + uuid.uuid4().hex
        scene_id, license_ref = _text(scene_id, "场景 id"), _text(license_ref, "许可引用", 500)
        with self._write():
            self._db.execute("INSERT INTO label_projects(id,name,classes,created_at,scene_id,license_ref,schema_revision) "
                             "VALUES (?,?,?,?,?,?,1)", (pid, name, _json(classes), _now(), scene_id, license_ref))
        return self.get_project(pid)

    def list_projects(self) -> list[dict]:
        with self._lock:
            return [self._project(r) for r in self._db.execute("SELECT * FROM label_projects ORDER BY created_at DESC,id")]

    def get_project(self, pid: str) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM label_projects WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise LabelError("标注项目不存在", 404)
        return self._project(row)

    @staticmethod
    def _asset(row) -> dict:
        out = dict(row)
        out.pop("original_file", None); out.pop("image_file", None)
        out["synthetic"] = bool(out["synthetic"])
        out["group_id"] = out["group_id"] or out["sha256"]
        for key in ("targets", "metadata", "provenance"):
            out[key] = json.loads(out[key])
        out["background"] = out["state"] == "background"
        return out

    def _asset_row(self, aid: str):
        row = self._db.execute("SELECT a.*,r.producer,r.actor,r.targets,r.provenance,r.created_at AS revision_created_at "
                               "FROM label_assets a JOIN label_revisions r ON r.asset_id=a.id AND r.revision=a.revision WHERE a.id=?", (aid,)).fetchone()
        if row is None:
            raise LabelError("标注图像不存在", 404)
        return row

    def get_asset(self, aid: str) -> dict:
        with self._lock:
            return self._asset(self._asset_row(aid))

    def image_path(self, aid: str) -> Path:
        with self._lock:
            return self._file(self._asset_row(aid)["image_file"])

    @quota_write(LabelError)
    def add_asset(self, pid: str, raw: bytes, name: str, metadata: dict | None = None) -> dict:
        project = self.get_project(pid)
        if not isinstance(raw, bytes) or not raw:
            raise LabelError("图片内容为空或不是二进制")
        if len(raw) > MAX_IMAGE_BYTES:
            raise LabelError("图片超过 20MB", 413)
        name = _text(name.replace("\\", "/").rsplit("/", 1)[-1] if isinstance(name, str) else name, "图片名称", 255, True)
        if metadata is not None and not isinstance(metadata, dict):
            raise LabelError("metadata 必须为对象")
        metadata = json.loads(_json(metadata or {}))
        synthetic = metadata.get("synthetic", False)
        if not isinstance(synthetic, bool):
            raise LabelError("synthetic 必须为布尔值")
        synthetic = synthetic or metadata.get("source_kind") in ("augment", "generated", "synthetic")
        license_ref = _text(metadata.get("license_ref", project["license_ref"]), "许可引用", 500)
        bucket = _text(metadata.get("illumination_bucket", "unknown"), "光照分桶", 64, True)
        sha = hashlib.sha256(raw).hexdigest()
        group = _text(metadata.get("group_id", ""), "来源组", 200) or sha
        if metadata.get("source_asset_id"):
            source = self.get_asset(_text(metadata["source_asset_id"], "来源图像 id", 100, True))
            if source["project_id"] != pid:
                raise LabelError("来源图像不属于此项目")
            group = source["group_id"]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    fmt = image.format
                    if fmt not in ("JPEG", "PNG", "WEBP", "BMP") or getattr(image, "n_frames", 1) != 1:
                        raise LabelError("仅支持单帧 JPEG/PNG/WebP/BMP 图片")
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise LabelError("图片像素超过 4000 万", 413)
                    image.load()
                    upright = ImageOps.exif_transpose(image)
                    if "A" in upright.getbands() or "transparency" in upright.info:
                        rgba = upright.convert("RGBA")
                        upright = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba)
                    clean = Image.new("RGB", upright.size)
                    clean.paste(upright.convert("RGB"))  # 不把原始 EXIF/GPS 写入预览/导出图
                    width, height = clean.size
                    buf = io.BytesIO(); clean.save(buf, format="PNG", compress_level=6)
                    normalized = buf.getvalue()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise LabelError("图片像素超过安全限制", 413) from None
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
            raise LabelError("图片无法完整解码") from None
        if self.quota is not None:
            self.quota.check(self.root, len(raw) + len(normalized))
        aid, now = "LA-" + uuid.uuid4().hex, _now()
        ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp"}[fmt]
        original_rel, image_rel = f"originals/{aid}{ext}", f"images/{aid}.png"
        written = []
        try:
            for relative, content in ((original_rel, raw), (image_rel, normalized)):
                path = self._file(relative, exists=False)
                with path.open("xb") as stream:
                    written.append(path)
                    stream.write(content)
            with self._write():
                self._db.execute("INSERT INTO label_assets (id,project_id,name,original_file,image_file,width,height,sha256,image_sha256,"
                                 "group_id,synthetic,license_ref,illumination_bucket,metadata,revision,state,created_at,updated_at) "
                                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'unlabeled',?,?)",
                                 (aid, pid, name, original_rel, image_rel, width, height, sha,
                                  hashlib.sha256(normalized).hexdigest(), group, int(synthetic), license_ref, bucket, _json(metadata), now, now))
                self._db.execute("INSERT INTO label_revisions(asset_id,revision,parent_revision,producer,actor,state,targets,provenance,created_at) "
                                 "VALUES (?,0,NULL,'system','','unlabeled','[]','{}',?)", (aid, now))
        except Exception:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return self.get_asset(aid)

    def list_assets(self, pid: str, state: str = "", limit: int = 100, offset: int = 0) -> dict:
        self.get_project(pid)
        if state and state not in STATES:
            raise LabelError("未知标注状态")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500 or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise LabelError("分页参数无效")
        where, params = "a.project_id=?", [pid]
        if state:
            where += " AND a.state=?"; params.append(state)
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM label_assets a WHERE {where}", params).fetchone()[0]
            rows = self._db.execute("SELECT a.*,r.producer,r.actor,r.targets,r.provenance,r.created_at AS revision_created_at "
                                    "FROM label_assets a JOIN label_revisions r ON r.asset_id=a.id AND r.revision=a.revision "
                                    f"WHERE {where} ORDER BY a.created_at,a.id LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        return {"items": [self._asset(r) for r in rows], "total": total}

    @staticmethod
    def _expected(row, expected_revision: int) -> None:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise LabelError("expected_revision 必须为非负整数")
        if row["revision"] != expected_revision:
            raise LabelError("标注已被其他操作更新，请重新读取后再保存", 409)

    def _append(self, row, targets: list, actor: str, producer: str, state: str, provenance: dict) -> None:
        revision, now = row["revision"] + 1, _now()
        self._db.execute("INSERT INTO label_revisions(asset_id,revision,parent_revision,producer,actor,state,targets,provenance,created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (row["id"], revision, row["revision"], producer, actor, state, _json(targets, 512 * 1024), _json(provenance), now))
        self._db.execute("UPDATE label_assets SET revision=?,state=?,updated_at=? WHERE id=?", (revision, state, now, row["id"]))

    def save_draft(self, aid: str, targets, expected_revision: int, actor: str,
                   producer: str = "human", provenance: dict | None = None) -> dict:
        actor = _text(actor, "操作者", 160, True)
        if producer not in ("human", "model", "agent"):
            raise LabelError("producer 必须为 human/model/agent")
        if provenance is not None and not isinstance(provenance, dict):
            raise LabelError("provenance 必须为对象")
        provenance = json.loads(_json(provenance or {}))
        with self._write():
            row = self._asset_row(aid)
            self._expected(row, expected_revision)
            if producer != "human" and row["state"] in ("confirmed", "background"):
                raise LabelError("已确认真值不能被机器建议覆盖", 409)
            clean = _targets(targets, self.get_project(row["project_id"])["classes"])
            self._append(row, clean, actor, producer, "draft" if producer == "human" else "suggested", provenance)
            return self._asset(self._asset_row(aid))

    def confirm(self, aid: str, expected_revision: int, actor: str, background: bool = False) -> dict:
        actor = _text(actor, "审核人", 160, True)
        if not isinstance(background, bool):
            raise LabelError("background 必须为布尔值")
        with self._write():
            row = self._asset_row(aid)
            self._expected(row, expected_revision)
            if row["state"] in ("confirmed", "background"):
                raise LabelError("当前修订已确认；编辑后再确认", 409)
            targets = _targets(json.loads(row["targets"]), self.get_project(row["project_id"])["classes"])
            if bool(targets) == background:
                raise LabelError("空目标必须明确确认为背景；有目标不能确认为背景")
            provenance = {"reviewed_revision": row["revision"], "source_producer": row["producer"],
                          "source_provenance": json.loads(row["provenance"])}
            self._append(row, targets, actor, "human", "background" if background else "confirmed", provenance)
            return self._asset(self._asset_row(aid))

    def revisions(self, aid: str) -> list[dict]:
        with self._lock:
            self._asset_row(aid)
            rows = self._db.execute("SELECT revision,parent_revision,producer,actor,state,targets,provenance,created_at "
                                    "FROM label_revisions WHERE asset_id=? ORDER BY revision DESC", (aid,)).fetchall()
        return [{**dict(r), "targets": json.loads(r["targets"]), "provenance": json.loads(r["provenance"])} for r in rows]

    def restore(self, aid: str, revision: int, expected_revision: int, actor: str) -> dict:
        actor = _text(actor, "操作者", 160, True)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise LabelError("revision 必须为非负整数")
        with self._write():
            row = self._asset_row(aid); self._expected(row, expected_revision)
            old = self._db.execute("SELECT targets,producer FROM label_revisions WHERE asset_id=? AND revision=?", (aid, revision)).fetchone()
            if old is None:
                raise LabelError("历史修订不存在", 404)
            self._append(row, json.loads(old["targets"]), actor, "human", "draft",
                         {"restore_revision": revision, "source_producer": old["producer"]})
            return self._asset(self._asset_row(aid))

    def _all_assets(self, pid: str) -> list[dict]:
        rows = self._db.execute("SELECT a.*,r.producer,r.actor,r.targets,r.provenance,r.created_at AS revision_created_at "
                                "FROM label_assets a JOIN label_revisions r ON r.asset_id=a.id AND r.revision=a.revision "
                                "WHERE a.project_id=? ORDER BY a.id", (pid,)).fetchall()
        return [self._asset(r) for r in rows]

    @staticmethod
    def _confirmed(asset: dict) -> bool:
        return asset["state"] in ("confirmed", "background") and asset["producer"] == "human" and bool(asset["actor"])

    def quality(self, pid: str) -> dict:
        with self._lock:
            project, assets = self.get_project(pid), self._all_assets(pid)
        states = {state: sum(a["state"] == state for a in assets) for state in STATES}
        confirmed = [a for a in assets if self._confirmed(a)]
        counts = []
        for cid, label in enumerate(project["classes"]):
            counts.append({"label_id": cid, "label": label,
                           "images": sum(any(t["label_id"] == cid for t in a["targets"]) for a in confirmed),
                           "targets": sum(t["label_id"] == cid for a in confirmed for t in a["targets"])})
        buckets, hashes = {}, {}
        for a in assets:
            buckets[a["illumination_bucket"]] = buckets.get(a["illumination_bucket"], 0) + 1
            hashes[a["sha256"]] = hashes.get(a["sha256"], 0) + 1
        missing_license = sum(not a["license_ref"] for a in assets)
        notes = []
        if not confirmed: notes.append("还没有人工确认的框或背景，不能导出")
        if len(confirmed) < len(assets): notes.append("未确认图像不会进入数据版本")
        if missing_license: notes.append("部分图像缺许可引用；引用不等于许可已审核")
        if any(a["synthetic"] for a in assets): notes.append("合成图及同源组仅可进入训练集")
        return {"project_id": pid, "total": len(assets), "states": states, "confirmed_total": len(confirmed),
                "exportable": len(confirmed), "unconfirmed": len(assets) - len(confirmed),
                "synthetic": sum(a["synthetic"] for a in assets), "missing_license": missing_license,
                "illumination_buckets": buckets, "class_counts": counts,
                "duplicate_sha_groups": sum(n > 1 for n in hashes.values()),
                "ready_for_export": bool(confirmed), "warnings": notes}

    @staticmethod
    def _split(assets: list[dict], seed: int, val_ratio: float) -> tuple[dict, dict]:
        parent = list(range(len(assets)))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]; i = parent[i]
            return i
        seen = {}
        for i, a in enumerate(assets):
            for key in (("group", a["group_id"]), ("sha", a["sha256"]), ("image", a["image_sha256"])):
                if not key[1]: continue
                if key in seen: parent[find(i)] = find(seen[key])
                else: seen[key] = i
        components = {}
        for i, asset in enumerate(assets): components.setdefault(find(i), []).append(asset)
        groups = {}
        for items in components.values():
            # 分组抽样由内容决定，重新导入同一批图片产生的新 UUID 不改变划分。
            key = hashlib.sha256(_json(sorted((a["image_sha256"], str(a["group_id"] or "")) for a in items)).encode()).hexdigest()
            groups[key] = {"asset_ids": sorted(a["id"] for a in items), "split": "train",
                           "synthetic": any(a["synthetic"] for a in items)}
        eligible = sorted(k for k, v in groups.items() if not v["synthetic"])
        random.Random(seed).shuffle(eligible)
        if val_ratio > 0 and len(groups) > 1 and eligible:
            max_val = len(eligible) if any(g["synthetic"] for g in groups.values()) else len(eligible) - 1
            n_val = max(1, min(max_val, round(len(groups) * val_ratio)))
            for key in eligible[:n_val]: groups[key]["split"] = "val"
        assignments = {aid: {"group": key, "split": group["split"]} for key, group in groups.items() for aid in group["asset_ids"]}
        return assignments, groups

    @staticmethod
    def _version(row) -> dict:
        manifest = json.loads(row["manifest"])
        assignments = manifest["assignments"]
        return {"id": row["id"], "project_id": row["project_id"], "sha256": row["sha256"], "manifest_sha256": row["sha256"],
                "zip_sha256": row["zip_sha256"], "created_at": row["created_at"], "actor": row["actor"],
                "seed": manifest["seed"], "val_ratio": manifest["val_ratio"], "asset_count": len(manifest["assets"]),
                "train_count": sum(v["split"] == "train" for v in assignments.values()),
                "val_count": sum(v["split"] == "val" for v in assignments.values()), "group_count": len(manifest["groups"]),
                "excluded_unconfirmed": len(manifest["excluded_assets"]), "warnings": manifest["warnings"]}

    @quota_write(LabelError)
    def create_version(self, pid: str, seed: int = 42, val_ratio: float = .2, actor: str = "") -> dict:
        if isinstance(seed, bool) or not isinstance(seed, int) or not -(2**31) <= seed < 2**31:
            raise LabelError("seed 必须为 32 位整数")
        if isinstance(val_ratio, bool) or not isinstance(val_ratio, (float, int)) or not math.isfinite(val_ratio) or not 0 <= val_ratio < 1:
            raise LabelError("val_ratio 必须是 0..1（不含 1）的有限数字")
        actor = _text(actor, "导出人")
        with self._write():
            project, all_assets = self.get_project(pid), self._all_assets(pid)
        assets = [a for a in all_assets if self._confirmed(a)]
        if not assets:
            raise LabelError("没有人工确认的框或背景可导出", 409)
        for a in assets:
            _targets(a["targets"], project["classes"])
            if (a["state"] == "background") != (not a["targets"]):
                raise LabelError("确认状态与目标内容不一致，拒绝导出", 409)
        assignments, groups = self._split(assets, seed, float(val_ratio))
        notes = []
        if not any(g["split"] == "val" for g in groups.values()): notes.append("没有独立验证组；仅导出训练数据，不满足训练/发布验收")
        if any(not a["license_ref"] for a in assets): notes.append("部分图像缺许可引用，训练前需补齐许可台账")
        excluded = [{"asset_id": a["id"], "revision": a["revision"], "state": a["state"]} for a in all_assets if not self._confirmed(a)]
        if excluded: notes.append("未确认图像已排除")
        fields = ("id", "revision", "state", "sha256", "image_sha256", "group_id", "synthetic", "license_ref",
                  "illumination_bucket", "width", "height", "targets", "producer", "actor", "revision_created_at", "provenance")
        manifest = {"schema_version": 1, "project": {k: project[k] for k in ("id", "name", "classes", "scene_id", "schema_revision")},
                    "assets": [{k: a[k] for k in fields} for a in assets], "assignments": assignments, "groups": groups,
                    "seed": seed, "val_ratio": float(val_ratio), "excluded_assets": excluded, "warnings": notes,
                    "policy": {"human_confirmed_only": True, "synthetic_train_only": True, "group_sha_isolation": True}}
        manifest_text = _json(manifest, 64 * 1024 * 1024)
        manifest_sha = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
        vid = "LV-" + manifest_sha[:32]
        with self._lock:
            existing = self._db.execute("SELECT * FROM label_versions WHERE id=? AND project_id=?", (vid, pid)).fetchone()
        if existing:
            self.export_path(pid, vid)
            return self._version(existing)
        payloads = {"manifest.json": manifest_text.encode("utf-8"), "classes.txt": ("\n".join(project["classes"]) + "\n").encode("utf-8"),
                    "dataset.yaml": ("path: .\ntrain: images/train\nval: images/val\nnc: " + str(len(project["classes"])) +
                                     "\nnames: " + json.dumps(project["classes"], ensure_ascii=False) + "\n").encode("utf-8")}
        backgrounds = []
        image_files = {}
        for a in assets:
            with self._lock:
                row = self._asset_row(a["id"])
                original, image = self._file(row["original_file"]), self._file(row["image_file"])
            if self._hash_file(image) != a["image_sha256"] or self._hash_file(original) != a["sha256"]:
                raise LabelError("原件或正向图像哈希不匹配，拒绝导出", 409)
            split = assignments[a["id"]]["split"]
            image_files[f"images/{split}/{a['id']}.png"] = (image, a["image_sha256"])
            label_path = f"labels/{split}/{a['id']}.txt"
            lines = []
            for t in a["targets"]:
                x0, y0, x1, y1 = t["xyxy_norm"]
                lines.append(f"{t['label_id']} {(x0+x1)/2:.8f} {(y0+y1)/2:.8f} {x1-x0:.8f} {y1-y0:.8f}")
            payloads[label_path] = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
            if a["state"] == "background": backgrounds.append(label_path)
        payloads["metadata.json"] = _json({"dataset_version_id": vid, "manifest_sha256": manifest_sha,
                                              "licenses": [], "license_refs": sorted({a["license_ref"] for a in assets if a["license_ref"]}),
                                              "reviewed_negatives": sorted(backgrounds)}).encode("utf-8")
        if self.quota is not None:
            self.quota.check(self.root, sum(len(value) for value in payloads.values()) +
                             sum(path.stat().st_size for path, _ in image_files.values()) +
                             len(manifest_text.encode("utf-8")))
        # 先写唯一临时目录；最终目录原子重命名，避免并发导出看到半个 ZIP。
        temp = self._file("versions/.tmp-" + uuid.uuid4().hex, exists=False)
        temp.mkdir()
        archive = temp / "dataset.zip"
        try:
            (temp / "manifest.json").write_text(manifest_text, encoding="utf-8")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as z:
                for filename in sorted([*payloads, *image_files]):
                    info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
                    info.create_system = 3; info.external_attr = 0o100644 << 16
                    if filename in payloads:
                        z.writestr(info, payloads[filename])
                    else:
                        path, expected = image_files[filename]
                        digest = hashlib.sha256()
                        with path.open("rb") as source, z.open(info, "w") as target:
                            while chunk := source.read(1024 * 1024):
                                digest.update(chunk); target.write(chunk)
                        if digest.hexdigest() != expected:
                            raise LabelError("图像在导出期间发生变化，拒绝生成版本", 409)
            zip_sha = self._hash_file(archive)
            if self.quota is not None:
                self.quota.check(self.root)
            with self._write():
                existing = self._db.execute("SELECT * FROM label_versions WHERE id=?", (vid,)).fetchone()
                if existing is None:
                    target = self._file(f"versions/{vid}", exists=False)
                    if target.exists():
                        # 崩溃可能发生在目录 rename 后、SQLite commit 前；只接管完整且字节一致的产物。
                        if (not target.is_dir() or not (target / "manifest.json").is_file() or not (target / "dataset.zip").is_file() or
                                self._hash_file(target / "manifest.json") != manifest_sha or self._hash_file(target / "dataset.zip") != zip_sha):
                            raise LabelError("已有版本目录不完整或内容冲突，请检查存储", 409)
                    else:
                        temp.rename(target)
                    self._db.execute("INSERT INTO label_versions VALUES (?,?,?,?,?,?,?,?)",
                                     (vid, pid, manifest_text, manifest_sha, f"versions/{vid}/dataset.zip", zip_sha, _now(), actor))
                    existing = self._db.execute("SELECT * FROM label_versions WHERE id=?", (vid,)).fetchone()
        finally:
            if temp.exists():
                for child in temp.iterdir(): child.unlink()
                temp.rmdir()
        return self._version(existing)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024): digest.update(chunk)
        return digest.hexdigest()

    def list_versions(self, pid: str) -> list[dict]:
        self.get_project(pid)
        with self._lock:
            return [self._version(r) for r in self._db.execute("SELECT * FROM label_versions WHERE project_id=? ORDER BY created_at DESC,id", (pid,))]

    def export_path(self, pid: str, vid: str) -> Path:
        with self._lock:
            row = self._db.execute("SELECT * FROM label_versions WHERE project_id=? AND id=?", (pid, vid)).fetchone()
        if row is None:
            raise LabelError("数据版本不存在", 404)
        archive = self._file(row["zip_file"])
        manifest = self._file(f"versions/{row['id']}/manifest.json")
        if (hashlib.sha256(manifest.read_bytes()).hexdigest() != row["sha256"] or
                hashlib.sha256(row["manifest"].encode("utf-8")).hexdigest() != row["sha256"] or
                self._hash_file(archive) != row["zip_sha256"]):
            raise LabelError("数据版本完整性校验失败", 409)
        return archive

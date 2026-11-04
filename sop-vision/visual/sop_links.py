"""SOP 文档-步骤关联：从文档生成步骤草稿、人工确认、文档更新影响提示。

草稿永远不会自动生效：确认只登记「已人工核对区域与证据」，把确认后的
步骤写进工位规则必须走现有配方版本流程（recipe_patch 导出 + 配方新增版本）。
区域占位符沿用 sop_templates 的「待映射:」前缀约定，未映射不得确认。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from .documents import DocumentError, authorize
from .sop_templates import PLACEHOLDER_PREFIX, skeleton_ids, step_skeleton, validate_steps

# 段落关键词 → 工步骨架 id；顺序即优先级，先命中先选。
KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("上料", "取料", "投料", "放入料"), "load"),
    (("拧紧", "锁附", "紧固", "扭矩", "螺母"), "fasten"),
    (("焊接", "焊缝", "锡焊"), "weld"),
    (("贴标", "标签", "铭牌", "扫码"), "label"),
    (("下料", "取出", "收料"), "unload"),
    (("包装", "装箱", "封口"), "pack"),
    (("点检", "设备状态", "开机", "开机检查"), "equipment-check"),
    (("检查", "检验", "目视", "外观"), "visual-check"),
]
# 新版本段落与草稿引用段落的最低相似度；低于该值视为「内容已变化」。
IMPACT_SIMILARITY = 0.6
MAX_DRAFTS = 500


def _now():
    return datetime.now(timezone.utc).isoformat()


class SopLinkError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def terms(text):
    # 与 documents.terms 相同的分词口径，保证影响对比与检索一致。
    lowered = text.lower()
    words = set(re.findall(r'[a-z0-9_-]{2,64}', lowered))
    for chunk in re.findall(r'[\u4e00-\u9fff]+', lowered):
        words.update(chunk[i:i+2] for i in range(len(chunk)-1))
    return words


def _conds(evidence: dict) -> list[dict]:
    mode = next(k for k in ("all", "any", "seq") if evidence.get(k))
    return evidence[mode]


def similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def generate_steps(paragraphs: list[dict]) -> tuple[list[dict], list[dict]]:
    """按段落顺序匹配关键词生成串行骨架步骤；返回 (steps, 引用段落快照)。

    文本与模板无匹配的段落原样保留在快照里，不虚构步骤；同名骨架重复出现
    时 id 加序号避免冲突。
    """
    chosen = []
    references = []
    counters: dict[str, int] = {}
    for para in paragraphs:
        text = para.get("text", "")
        match = next((sid for keys, sid in KEYWORDS if any(k in text for k in keys)), None)
        if match:
            counters[match] = counters.get(match, 0) + 1
            step_id = match if counters[match] == 1 else f"{match}-{counters[match]}"
            step = step_skeleton(match)
            step["id"] = step_id
            if chosen:
                step["after"] = [chosen[-1]["id"]]
            else:
                step["after"] = []
            chosen.append(step)
        references.append({"paragraph": para.get("paragraph"), "text": text,
                           "step_id": match})
    if not chosen:
        raise SopLinkError("文档段落没有匹配到可识别的工步；请人工在草稿中添加步骤")
    validate_steps(chosen)
    return chosen, references


class SopLinkStore:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS sop_step_drafts (
            id TEXT PRIMARY KEY, document_id TEXT, version_id TEXT, version_number INTEGER,
            station_id TEXT, status TEXT, steps TEXT, paragraphs TEXT, template_id TEXT,
            created TEXT, actor TEXT, confirmed TEXT, confirm_actor TEXT)""")
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def _row(self, row):
        body = {
            "id": row[0], "document_id": row[1], "version_id": row[2],
            "version_number": row[3], "station_id": row[4], "status": row[5],
            "steps": json.loads(row[6]), "paragraphs": json.loads(row[7]),
            "template_id": row[8], "created": row[9], "actor": row[10],
        }
        if row[11]:
            body["confirmed"] = row[11]
            body["confirm_actor"] = row[12]
        return body

    def _get(self, did):
        row = self.db.execute("SELECT * FROM sop_step_drafts WHERE id=?", (did,)).fetchone()
        if not row:
            raise SopLinkError("步骤草稿不存在", 404)
        return self._row(row)

    def create(self, version_id: str, version_number: int, document_id: str, station_id: str,
               paragraphs: list[dict], role: str, actor: str, template_id: str | None = None):
        authorize(role, write=True)
        steps, references = generate_steps(paragraphs)
        with self.lock:
            count = self.db.execute("SELECT COUNT(*) FROM sop_step_drafts").fetchone()[0]
            if count >= MAX_DRAFTS:
                raise SopLinkError("草稿容量已达上限，请清理已弃用草稿", 409)
            record = {"id": uuid.uuid4().hex, "document_id": document_id,
                      "version_id": version_id, "version_number": version_number,
                      "station_id": station_id, "status": "draft",
                      "steps": steps, "paragraphs": references,
                      "template_id": template_id, "created": _now(), "actor": actor}
            self.db.execute("INSERT INTO sop_step_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (record["id"], document_id, version_id, version_number, station_id,
                             "draft", json.dumps(steps, ensure_ascii=False),
                             json.dumps(references, ensure_ascii=False), template_id,
                             record["created"], actor, None, None))
            self.db.commit()
        return record

    def update_steps(self, did: str, steps: list[dict], role: str, actor: str):
        authorize(role, write=True)
        if self._get(did)["status"] != "draft":
            raise SopLinkError("仅草稿状态可以编辑步骤", 409)
        validate_steps(steps)  # dsl.Step 校验失败会抛 ValueError，路由层转 422。
        with self.lock:
            self.db.execute("UPDATE sop_step_drafts SET steps=?, actor=? WHERE id=?",
                            (json.dumps(steps, ensure_ascii=False), actor, did))
            self.db.commit()
        return self._get(did)

    def confirm(self, did: str, role: str, actor: str, zones: list[str]):
        authorize(role, write=True)
        draft = self._get(did)
        if draft["status"] != "draft":
            raise SopLinkError("草稿已确认或已弃用", 409)
        validate_steps(draft["steps"])
        known = set(zones)
        for step in draft["steps"]:
            for cond in _conds(step["evidence"]):
                (kind, body), = cond.items()
                zone = body.get("zone")
                if zone and zone.startswith(PLACEHOLDER_PREFIX):
                    raise SopLinkError(
                        f"步骤 {step['id']} 区域仍为占位符「{zone}」，必须先映射为工位实际区域", 422)
                if zone and zone not in known:
                    raise SopLinkError(f"步骤 {step['id']} 引用了工位不存在的区域「{zone}」", 422)
        with self.lock:
            self.db.execute("UPDATE sop_step_drafts SET status='confirmed', confirmed=?, confirm_actor=? WHERE id=?",
                            (_now(), actor, did))
            self.db.commit()
        return self._get(did)

    def discard(self, did: str, role: str):
        authorize(role, write=True)
        if self._get(did)["status"] == "confirmed":
            raise SopLinkError("已确认草稿不能弃用；请创建新草稿替代", 409)
        with self.lock:
            self.db.execute("UPDATE sop_step_drafts SET status='discarded' WHERE id=?", (did,))
            self.db.commit()
        return self._get(did)

    def get(self, did: str, role: str):
        authorize(role)
        return self._get(did)

    def list(self, role: str, document_id=None, station_id=None):
        authorize(role)
        sql = "SELECT * FROM sop_step_drafts WHERE (? IS NULL OR document_id=?) AND (? IS NULL OR station_id=?) ORDER BY rowid DESC LIMIT 500"
        with self.lock:
            rows = self.db.execute(sql, (document_id, document_id, station_id, station_id)).fetchall()
        return [self._row(r) for r in rows]

    def recipe_patch(self, did: str, role: str) -> dict:
        authorize(role, write=True)
        draft = self._get(did)
        if draft["status"] != "confirmed":
            raise SopLinkError("仅已确认草稿可导出配方步骤", 409)
        return {"steps": draft["steps"], "version_id": draft["version_id"],
                "document_id": draft["document_id"], "station_id": draft["station_id"]}

    def impact(self, role: str, document_id: str, versions: list[dict], paragraphs_of):
        """文档更新影响检测：对每个已确认草稿，把引用段落与同文档最新版本段落对比。

        versions 是 DocumentStore.list(role, document_id) 的结果（含状态）；
        paragraphs_of(version_id) 返回该版本段落。仅提示，不修改任何规则。
        """
        authorize(role)
        latest = next((v for v in versions if v["status"] == "published"), None) \
            or (versions[0] if versions else None)
        if latest is None:
            raise SopLinkError("文档没有可用版本", 404)
        latest_paragraphs = paragraphs_of(latest["id"])
        latest_terms = [terms(p["text"]) for p in latest_paragraphs]
        findings = []
        for draft in self.list(role, document_id=document_id):
            if draft["status"] != "confirmed":
                continue
            for ref in draft["paragraphs"]:
                ref_terms = terms(ref["text"])
                best = max((similarity(ref_terms, cand) for cand in latest_terms), default=0.0)
                if best < IMPACT_SIMILARITY:
                    # step_id 是生成草稿时该段落命中的工步锚点；None 表示未匹配段落。
                    linked = [ref["step_id"]] if ref.get("step_id") else []
                    findings.append({
                        "draft_id": draft["id"], "station_id": draft["station_id"],
                        "step_ids": linked,
                        "paragraph": ref["paragraph"], "similarity": round(best, 4),
                        "documented_text": ref["text"][:200],
                        "latest_version": latest["version"],
                    })
        return {"document_id": document_id, "latest_version_id": latest["id"],
                "latest_version": latest["version"], "affected": findings}


def document_paragraph_snapshot(store, vid: str, role: str, wanted=None) -> list[dict]:
    """取文档版本段落快照；wanted 限制段号时保持原顺序，空文档拒绝生成草稿。"""
    paragraphs = store.paragraphs(vid, role)
    if wanted is not None:
        keep = set(wanted)
        paragraphs = [p for p in paragraphs if p["paragraph"] in keep]
        if len(paragraphs) != len(keep):
            missing = sorted(keep - {p["paragraph"] for p in paragraphs})
            raise DocumentError(f"段落不存在：{missing[:10]}", 404)
    if not paragraphs:
        raise DocumentError("没有可用的文档段落", 400)
    return paragraphs

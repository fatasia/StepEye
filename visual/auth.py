"""登录认证与 RBAC:PBKDF2 口令 + 会话令牌,零新依赖。

角色三级:viewer(只读)< operator(操作)< admin(用户管理)。
默认关闭;serve --auth 或 VISUAL_AUTH=1 启用。启用后除白名单外全部 401,
写端点要求 operator+,/api/users* 要求 admin。密码只存 PBKDF2-SHA256(16B 盐,20 万轮)。
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import threading
import time
from pathlib import Path

PBKDF2_ROUNDS = 200_000
SESSION_TTL_S = 12 * 3600
ROLES = ("viewer", "operator", "admin")
_ROLE_RANK = {r: i for i, r in enumerate(ROLES)}
_KEEP_SCOPE = object()


def _hash_pw(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)


class AuthError(Exception):
    def __init__(self, msg: str, status: int = 401):
        super().__init__(msg)
        self.status = status


class AuthStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY,"
                         " pw_salt BLOB, pw_hash BLOB, role TEXT, created TEXT)")
        if "projects" not in {r[1] for r in self._db.execute("PRAGMA table_info(users)")}:
            self._db.execute("ALTER TABLE users ADD COLUMN projects TEXT NOT NULL DEFAULT 'null'")
        self._db.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY,"
                         " username TEXT, expires REAL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS mcp_tokens (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE,"
                         " username TEXT, name TEXT, scopes TEXT, projects TEXT, expires REAL, revoked INTEGER DEFAULT 0)")
        self._db.execute("CREATE TABLE IF NOT EXISTS mcp_audit (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                         " at REAL, token_id TEXT, operation TEXT, status INTEGER)")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _user_write(self):
        # BEGIN IMMEDIATE protects the last-admin check across separate DB connections.
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    @staticmethod
    def _username(username: str) -> None:
        if (not isinstance(username, str) or not 1 <= len(username) <= 64
                or any(not (c.isalnum() or c in "._-") for c in username)):
            raise ValueError("用户名须为1–64位中文、字母、数字或 . _ -")

    def _keep_admin(self, username: str) -> None:
        row = self._db.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if row and row[0] == "admin":
            count = self._db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            if count <= 1:
                raise AuthError("至少保留一个管理员，先创建其他管理员再操作", 409)

    # ---- 用户 ----
    def set_user(self, username: str, password: str, role: str = "operator",
                 project_ids=_KEEP_SCOPE) -> None:
        self._username(username)
        if role not in ROLES:
            raise ValueError(f"角色必须是 {ROLES} 之一")
        if not isinstance(password, str) or len(password) < 8:
            raise ValueError("密码至少 8 位")
        if len(password) > 256:
            raise ValueError("密码最多256位")
        if project_ids is not _KEEP_SCOPE:
            project_ids = self._projects(project_ids, role)
        salt = secrets.token_bytes(16)
        hashed = _hash_pw(password, salt)
        with self._user_write():
            if role != "admin":
                self._keep_admin(username)
            previous = self._db.execute("SELECT projects FROM users WHERE username=?", (username,)).fetchone()
            projects = project_ids if project_ids is not _KEEP_SCOPE else json.loads(previous[0]) if previous else None
            if role == "admin": projects = None
            self._db.execute("INSERT INTO users(username,pw_salt,pw_hash,role,created,projects) VALUES (?,?,?,?,?,?) "
                             "ON CONFLICT(username) DO UPDATE SET pw_salt=excluded.pw_salt,"
                             "pw_hash=excluded.pw_hash,role=excluded.role,projects=excluded.projects",
                             (username, salt, hashed, role, time.strftime("%Y-%m-%dT%H:%M:%S"), json.dumps(projects)))
            # Password/role changes revoke both browser sessions and delegated tokens.
            self._db.execute("DELETE FROM sessions WHERE username=?", (username,))
            self._db.execute("UPDATE mcp_tokens SET revoked=1 WHERE username=?", (username,))

    @staticmethod
    def _projects(project_ids: list[str] | None, role: str) -> list[str] | None:
        if project_ids is None: return None
        if (not isinstance(project_ids, list) or len(project_ids) > 100
                or any(not isinstance(p, str) or not p or len(p) > 100
                       or any(not (c.isalnum() or c in "._-") for c in p) for p in project_ids)):
            raise ValueError("项目范围须为最多100个有效项目ID")
        if role == "admin":
            raise ValueError("管理员使用全局数据范围")
        return sorted(set(project_ids))

    def set_scope(self, username: str, project_ids: list[str] | None) -> None:
        """管理员修改范围不重设密码；撤销既有会话，含已建立的WS会话。"""
        self._username(username)
        with self._user_write():
            row = self._db.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if row is None: raise AuthError("用户不存在", 404)
            projects = self._projects(project_ids, row[0])
            self._db.execute("UPDATE users SET projects=? WHERE username=?", (json.dumps(projects), username))
            self._db.execute("DELETE FROM sessions WHERE username=?", (username,))
            self._db.execute("UPDATE mcp_tokens SET revoked=1 WHERE username=?", (username,))

    def delete_user(self, username: str) -> bool:
        self._username(username)
        with self._user_write():
            self._keep_admin(username)
            cur = self._db.execute("DELETE FROM users WHERE username=?", (username,))
            self._db.execute("DELETE FROM sessions WHERE username=?", (username,))
            self._db.execute("DELETE FROM mcp_tokens WHERE username=?", (username,))
            return cur.rowcount > 0

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT username, role, created, projects FROM users ORDER BY username").fetchall()
        return [{"username": r[0], "role": r[1], "created": r[2], "project_ids": json.loads(r[3])} for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ---- 登录/会话 ----
    def login(self, username: str, password: str) -> str:
        # Validation, password verification and session creation share the write lock:
        # a concurrent reset cannot be followed by a session issued from the old hash.
        if (not isinstance(username, str) or len(username) > 64
                or not isinstance(password, str) or len(password) > 256):
            raise AuthError("用户名或密码错误", 401)
        with self._user_write():
            row = self._db.execute("SELECT pw_salt, pw_hash, role FROM users WHERE username=?", (username,)).fetchone()
            if row is None or not hmac.compare_digest(row[1], _hash_pw(password, row[0])):
                raise AuthError("用户名或密码错误", 401)
            token = secrets.token_urlsafe(32)
            th = hashlib.sha256(token.encode()).hexdigest()
            self._db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?)",
                             (th, username, time.time() + SESSION_TTL_S))
        return token

    def verify_session(self, token: str) -> dict | None:
        if not token:
            return None
        th = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            row = self._db.execute("SELECT username, expires FROM sessions WHERE token_hash=?", (th,)).fetchone()
            if row is None or row[1] < time.time():
                return None
            u = self._db.execute("SELECT role, projects FROM users WHERE username=?", (row[0],)).fetchone()
        if u is None:
            return None
        projects = None if u[0] == "admin" else json.loads(u[1])
        return {"username": row[0], "role": u[0], "project_ids": projects,
                "scope_type": "all" if projects is None else "projects"}

    def logout(self, token: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE token_hash=?",
                             (hashlib.sha256(token.encode()).hexdigest(),))
            self._db.commit()

    # ---- MCP 专用授权：只存摘要，不与浏览器会话混用 ----
    def issue_mcp_token(self, username: str, name: str, scopes: list[str], projects: list[str],
                        expires_in_s: float = 3600) -> dict:
        if not name.strip() or len(name) > 80 or set(scopes) - {"read", "images", "draft"} or "read" not in scopes:
            raise ValueError("名称1–80字符；scope须含read，仅支持read/images/draft")
        if not math.isfinite(expires_in_s) or not 60 <= expires_in_s <= 30 * 86400:
            raise ValueError("令牌有效期必须为60秒–30天")
        token = "se_mcp_" + secrets.token_urlsafe(32)
        token_id = secrets.token_hex(12)
        expires = time.time() + expires_in_s
        scopes, projects = sorted(set(scopes)), sorted(set(projects))
        with self._lock:
            user = self._db.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if user is None or user[0] != "admin":
                raise AuthError("MCP令牌只能由管理员创建", 403)
            self._db.execute("INSERT INTO mcp_tokens VALUES (?,?,?,?,?,?,?,0)",
                             (token_id, hashlib.sha256(token.encode()).hexdigest(), username, name.strip(),
                              json.dumps(scopes), json.dumps(projects), expires))
            self._db.execute("INSERT INTO mcp_audit(at,token_id,operation,status) VALUES (?,?,?,?)",
                             (time.time(), token_id, "issued", 201))
            self._db.commit()
        return {"id": token_id, "name": name.strip(), "scopes": scopes, "project_ids": projects,
                "expires_at": expires, "token": token}

    def verify_mcp_token(self, token: str) -> dict | None:
        if not token.startswith("se_mcp_"):
            return None
        with self._lock:
            row = self._db.execute("SELECT t.id,t.username,t.scopes,t.projects,t.expires,t.revoked,u.role "
                                   "FROM mcp_tokens t JOIN users u ON u.username=t.username WHERE token_hash=?",
                                   (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None or row[4] <= time.time() or row[5] or row[6] != "admin":
            return None
        return {"username": row[1], "role": "viewer", "mcp_token_id": row[0],
                "scopes": json.loads(row[2]), "project_ids": json.loads(row[3])}

    def list_mcp_tokens(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id,username,name,scopes,projects,expires,revoked FROM mcp_tokens ORDER BY rowid DESC").fetchall()
        return [{"id": r[0], "username": r[1], "name": r[2], "scopes": json.loads(r[3]),
                 "project_ids": json.loads(r[4]), "expires_at": r[5], "revoked": bool(r[6])} for r in rows]

    def revoke_mcp_token(self, token_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE mcp_tokens SET revoked=1 WHERE id=?", (token_id,))
            if cur.rowcount:
                self._db.execute("INSERT INTO mcp_audit(at,token_id,operation,status) VALUES (?,?,?,?)",
                                 (time.time(), token_id, "revoked", 200))
            self._db.commit()
        return cur.rowcount > 0

    def audit_mcp(self, token_id: str, operation: str, status: int) -> None:
        with self._lock:
            self._db.execute("INSERT INTO mcp_audit(at,token_id,operation,status) VALUES (?,?,?,?)",
                             (time.time(), token_id, operation[:200], status))
            self._db.commit()

    def mcp_audit(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT at,token_id,operation,status FROM mcp_audit ORDER BY id DESC LIMIT ?",
                                    (max(1, min(limit, 500)),)).fetchall()
        return [{"at": r[0], "token_id": r[1], "operation": r[2], "status": r[3]} for r in rows]

    # ---- 授权 ----
    @staticmethod
    def check(user: dict | None, need: str = "viewer") -> None:
        if user is None:
            raise AuthError("未登录或会话已过期", 401)
        if _ROLE_RANK[user["role"]] < _ROLE_RANK[need]:
            raise AuthError(f"需要 {need} 及以上角色", 403)

"""登录认证与 RBAC:PBKDF2 口令 + 会话令牌,零新依赖。

角色三级:viewer(只读)< operator(操作)< admin(用户管理)。
默认关闭;serve --auth 或 VISUAL_AUTH=1 启用。启用后除白名单外全部 401,
写端点要求 operator+,/api/users* 要求 admin。密码只存 PBKDF2-SHA256(16B 盐,20 万轮)。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from pathlib import Path

PBKDF2_ROUNDS = 200_000
SESSION_TTL_S = 12 * 3600
ROLES = ("viewer", "operator", "admin")
_ROLE_RANK = {r: i for i, r in enumerate(ROLES)}


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
        self._db.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY,"
                         " username TEXT, expires REAL)")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- 用户 ----
    def set_user(self, username: str, password: str, role: str = "operator") -> None:
        if role not in ROLES:
            raise ValueError(f"角色必须是 {ROLES} 之一")
        if len(password) < 8:
            raise ValueError("密码至少 8 位")
        salt = secrets.token_bytes(16)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?,?)",
                             (username, salt, _hash_pw(password, salt), role,
                              time.strftime("%Y-%m-%dT%H:%M:%S")))
            self._db.commit()

    def delete_user(self, username: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM users WHERE username=?", (username,))
            self._db.execute("DELETE FROM sessions WHERE username=?", (username,))
            self._db.commit()
        return cur.rowcount > 0

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT username, role, created FROM users ORDER BY username").fetchall()
        return [{"username": r[0], "role": r[1], "created": r[2]} for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ---- 登录/会话 ----
    def login(self, username: str, password: str) -> str:
        with self._lock:
            row = self._db.execute("SELECT pw_salt, pw_hash, role FROM users WHERE username=?", (username,)).fetchone()
        if row is None or not hmac.compare_digest(row[1], _hash_pw(password, row[0])):
            raise AuthError("用户名或密码错误", 401)
        token = secrets.token_urlsafe(32)
        th = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?)",
                             (th, username, time.time() + SESSION_TTL_S))
            self._db.commit()
        return token

    def verify_session(self, token: str) -> dict | None:
        if not token:
            return None
        th = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            row = self._db.execute("SELECT username, expires FROM sessions WHERE token_hash=?", (th,)).fetchone()
            if row is None or row[1] < time.time():
                return None
            u = self._db.execute("SELECT role FROM users WHERE username=?", (row[0],)).fetchone()
        if u is None:
            return None
        return {"username": row[0], "role": u[0]}

    def logout(self, token: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE token_hash=?",
                             (hashlib.sha256(token.encode()).hexdigest(),))
            self._db.commit()

    # ---- 授权 ----
    @staticmethod
    def check(user: dict | None, need: str = "viewer") -> None:
        if user is None:
            raise AuthError("未登录或会话已过期", 401)
        if _ROLE_RANK[user["role"]] < _ROLE_RANK[need]:
            raise AuthError(f"需要 {need} 及以上角色", 403)

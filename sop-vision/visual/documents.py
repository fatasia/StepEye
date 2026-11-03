"""Immutable document originals and role-filtered local paragraph retrieval.

Tokenization/retrieval adapted from weihu phm_runtime/knowledge.py.
Copyright (c) 2026 fatasia. MIT License: documents_LICENSE.txt.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from .documents_parse import MAX_BYTES, parse_file


class DocumentError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def terms(text):
    lowered = text.lower()
    words = set(re.findall(r'[a-z0-9_-]{2,64}', lowered))
    for chunk in re.findall(r'[\u4e00-\u9fff]+', lowered):
        words.update(chunk[i:i+2] for i in range(len(chunk)-1))
    return words


def authorize(role, write=False, admin=False):
    if role not in ('viewer', 'operator', 'admin') or (write and role == 'viewer') or (admin and role != 'admin'):
        raise DocumentError('当前角色无权执行文档操作', 403)


class DocumentStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.files = self.root / 'originals'
        self.files.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.root/'documents.db'), check_same_thread=False, timeout=10)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('CREATE TABLE IF NOT EXISTS document_versions (id TEXT PRIMARY KEY, document_id TEXT, version INTEGER, status TEXT, body TEXT, UNIQUE(document_id,version))')
        self._db.execute('CREATE TABLE IF NOT EXISTS document_paragraphs (version_id TEXT, paragraph INTEGER, line_start INTEGER, line_end INTEGER, text TEXT, PRIMARY KEY(version_id,paragraph))')
        self._db.execute('CREATE TABLE IF NOT EXISTS document_terms (term TEXT, version_id TEXT, paragraph INTEGER, PRIMARY KEY(term,version_id,paragraph))')
        self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    def _record(self, row):
        body = json.loads(row[0])
        body['status'] = row[1]
        return body

    def get_version(self, vid, role):
        authorize(role)
        with self._lock:
            row = self._db.execute('SELECT body,status FROM document_versions WHERE id=?', (vid,)).fetchone()
        if not row or (role == 'viewer' and row[1] != 'published'):
            raise DocumentError('文档版本不存在或不可见', 404)
        return self._record(row)

    def list(self, role, document_id=None):
        authorize(role)
        with self._lock:
            rows = self._db.execute("SELECT body,status FROM document_versions WHERE (? != 'viewer' OR status='published') AND (? IS NULL OR document_id=?) ORDER BY rowid DESC LIMIT 1000", (role, document_id, document_id)).fetchall()
        return [self._record(row) for row in rows]

    def upload(self, filename, data, metadata, role, actor, document_id=None):
        authorize(role, write=True)
        suffix = Path(filename.replace('\\','/')).suffix.lower()
        if suffix not in ('.txt', '.md', '.pdf', '.docx'):
            raise DocumentError('仅支持 txt/md/pdf/docx')
        if not data or len(data) > MAX_BYTES:
            raise DocumentError('文件为空或超过 10MB', 413)
        for key in ('title', 'station', 'device', 'product'):
            value = metadata.get(key, '')
            if not isinstance(value, str) or len(value) > 200 or '\x00' in value:
                raise DocumentError('文档名称或关联字段无效')
        if not metadata.get('title', '').strip():
            raise DocumentError('文档标题不能为空')
        if document_id and not self.list(role, document_id):
            raise DocumentError('文档不存在', 404)
        vid = uuid.uuid4().hex
        path = self.files / (vid + suffix)
        # Filename never controls the disk path; only opaque server IDs are used.
        path.write_bytes(data)
        try:
            text = parse_file(path)
            # Wrap unusually long lines before retrieval chunking; original stays unchanged.
            lines = [part for line in text.splitlines() for part in ( [line[i:i+300] for i in range(0,len(line),300)] or [''])]
            chunks = []
            for start in range(0, len(lines), 8):
                snippet = '\n'.join(lines[start:start+12])
                if snippet.strip():
                    chunks.append((len(chunks)+1, start+1, min(start+12,len(lines)), snippet))
            if len(chunks) > 1000:
                raise DocumentError('文档段落数超过 1000')
            with self._lock:
                try:
                    self._db.execute('BEGIN IMMEDIATE')
                    count = self._db.execute('SELECT COUNT(*) FROM document_versions').fetchone()[0]
                    if count >= 5000:
                        raise DocumentError('文档版本容量已达 5000，需管理员整理存储', 409)
                    did = document_id or uuid.uuid4().hex
                    version = self._db.execute('SELECT COALESCE(MAX(version),0)+1 FROM document_versions WHERE document_id=?',(did,)).fetchone()[0]
                    record = {'id':vid,'document_id':did,'version':version,'filename':Path(filename.replace('\\','/')).name[:200],
                        'title':metadata['title'],'station':metadata.get('station',''),'device':metadata.get('device',''),
                        'product':metadata.get('product',''),'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),
                        'suffix':suffix,'created':datetime.now(timezone.utc).isoformat(),'actor':actor,'paragraphs':len(chunks)}
                    self._db.execute('INSERT INTO document_versions VALUES(?,?,?,?,?)',(vid,did,version,'draft',json.dumps(record)))
                    for number, first, last, snippet in chunks:
                        self._db.execute('INSERT INTO document_paragraphs VALUES(?,?,?,?,?)',(vid,number,first,last,snippet))
                        self._db.executemany('INSERT INTO document_terms VALUES(?,?,?)',[(term,vid,number) for term in terms(snippet)])
                    self._db.commit()
                except Exception:
                    self._db.rollback()
                    raise
            return {**record, 'status':'draft'}
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def set_status(self, vid, status, role):
        authorize(role, admin=True)
        if status not in ('published','archived'):
            raise DocumentError('状态仅允许 published/archived')
        record = self.get_version(vid, role)
        with self._lock:
            try:
                self._db.execute('BEGIN IMMEDIATE')
                if status == 'published':
                    self._db.execute("UPDATE document_versions SET status='archived' WHERE document_id=? AND status='published'",(record['document_id'],))
                self._db.execute('UPDATE document_versions SET status=? WHERE id=?',(status,vid))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return self.get_version(vid, role)

    def original(self, vid, role):
        record = self.get_version(vid, role)
        path = self.files/(record['id']+record['suffix'])
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise DocumentError('原文件缺失或校验失败', 409)
        return record, path

    def paragraphs(self, vid, role):
        self.get_version(vid, role)
        with self._lock:
            return [{'paragraph':r[0], 'line_start':r[1], 'line_end':r[2], 'text':r[3]} for r in self._db.execute('SELECT paragraph,line_start,line_end,text FROM document_paragraphs WHERE version_id=? ORDER BY paragraph',(vid,))]

    def search(self, query, role, limit=5):
        authorize(role)
        if not isinstance(query, str) or not 1 <= len(query) <= 200 or not 1 <= limit <= 10:
            raise DocumentError('查询 1–200 字符，最多 10 项')
        wanted = sorted(terms(query))[:100]
        if not wanted:
            return []
        # Role predicate is applied inside SQL before ranking; drafts cannot leak via hits.
        marks = ','.join('?' for _ in wanted)
        sql = f'''SELECT v.body,p.paragraph,p.line_start,p.line_end,p.text,COUNT(*) AS score
            FROM document_terms t JOIN document_versions v ON v.id=t.version_id
            JOIN document_paragraphs p ON p.version_id=t.version_id AND p.paragraph=t.paragraph
            WHERE t.term IN ({marks}) AND (v.status='published' OR (?!='viewer' AND v.status='draft'))
            GROUP BY t.version_id,t.paragraph HAVING COUNT(*)>=? ORDER BY score DESC,v.rowid DESC,p.paragraph LIMIT ?'''
        with self._lock:
            rows=self._db.execute(sql, [*wanted,role,min(2,len(wanted)),limit]).fetchall()
        return [{'document':json.loads(r[0])['title'],'document_id':json.loads(r[0])['document_id'],
                 'version_id':json.loads(r[0])['id'],'version':json.loads(r[0])['version'],
                 'paragraph':r[1],'line_start':r[2],'line_end':r[3],'text':r[4], 'score':r[5]} for r in rows]

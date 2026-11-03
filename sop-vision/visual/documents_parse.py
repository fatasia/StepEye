"""Bounded document extraction; files are data, never executable instructions."""
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile
from xml.etree import ElementTree

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 200000


def extract(data, suffix):
    if not data or len(data) > MAX_BYTES:
        raise ValueError('文件为空或超过 10MB')
    if suffix in ('.txt', '.md'):
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ValueError('文本文件须使用 UTF-8 编码') from exc
    elif suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 500 or len({e.filename for e in entries}) != len(entries):
                raise ValueError('DOCX 文件条目过多或重复')
            total = 0
            for item in entries:
                total += item.file_size
                if (item.flag_bits & 1 or item.file_size > 10*1024*1024
                        or item.file_size / max(item.compress_size, 1) > 100
                        or total > 30*1024*1024):
                    raise ValueError('DOCX 压缩大小或压缩比超限')
                path = item.filename.replace('\\', '/')
                if path.startswith('/') or '..' in path.split('/') or ':' in path:
                    raise ValueError('DOCX 包含非法路径')
            raw = archive.read('word/document.xml')
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('DOCX 不接受 XML 实体或 DTD')
            node = ElementTree.fromstring(raw)
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(p.itertext()) for p in node.iter(ns+'p'))
    elif suffix == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError('PDF 解析需要可选依赖 pypdf') from exc
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError('不接受加密 PDF')
        if len(reader.pages) > 200:
            raise ValueError('PDF 超过 200 页')
        parts = []
        length = 0
        for page in reader.pages:
            value = page.extract_text() or ''
            length += len(value)
            if length > MAX_TEXT:
                raise ValueError('提取文本超过 20 万字符')
            parts.append(value)
        text = '\n'.join(parts)
    else:
        raise ValueError('仅支持 txt/md/pdf/docx')
    if not text.strip():
        raise ValueError('没有可提取文本；扫描件需要先 OCR')
    if len(text) > MAX_TEXT or '\x00' in text:
        raise ValueError('文本超过上限或包含 NUL 字符')
    return text


def parse_file(path):
    """Isolated parser with timeout/RSS guard; no untrusted document in server parser."""
    try:
        import psutil
    except ImportError as exc:
        raise ValueError('文档解析需要可选依赖 psutil') from exc
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    start = time.monotonic()
    try:
        while True:
            try:
                rss = psutil.Process(process.pid).memory_info().rss
            except psutil.NoSuchProcess:
                rss = 0
            if rss > 384 * 1024 * 1024 or time.monotonic() - start > 15:
                raise ValueError('文档解析超时或内存超限')
            try:
                output, _ = process.communicate(timeout=.05)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0 or len(output) > 2 * 1024 * 1024:
            raise ValueError('文档解析失败')
        result = json.loads(output)
        if 'error' in result:
            raise ValueError(result['error'])
        return result['text']
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


if __name__ == '__main__':
    try:
        source = Path(sys.argv[1])
        result = {'text': extract(source.read_bytes(), source.suffix.lower())}
    except Exception as exc:
        result = {'error': str(exc) if isinstance(exc, ValueError) else '文件格式损坏或解析失败'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

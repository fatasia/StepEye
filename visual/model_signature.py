# Adapted from weihu phm_core/signing.py, MIT, Copyright (c) 2026 fatasia.
"""Ed25519 对目录清单签名；信任公钥由部署方提供，不信任包内自带公钥。"""

import base64
import hashlib
import json
import os
from pathlib import Path

SIGNATURE = "signature.json"
TRUST_ENV = "STEPEYE_MODEL_TRUST_KEYS"


def model_trust_keys():
    """Unset means legacy mode; configured-but-invalid is never a bypass."""
    raw = os.environ.get(TRUST_ENV)
    if raw is None:
        return None
    try:
        keys = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{TRUST_ENV} 须为公钥路径 JSON 数组") from error
    if (not isinstance(keys, list) or not 1 <= len(keys) <= 8
            or any(not isinstance(k, str) or not Path(k).is_absolute() for k in keys)):
        raise ValueError(f"{TRUST_ENV} 须包含1至8个公钥绝对路径")
    return keys


def verify_model_policy(model):
    """Verify once at load/release boundaries, never on the per-frame path."""
    keys = model_trust_keys()
    if keys is None:
        return {"verified": False, "status": "not_required"}
    model = Path(model)
    if model.is_symlink() or not model.is_file():
        raise ValueError("签名模型须为普通文件")
    verify_trust_set(model.parent, keys)
    return {"verified": True, "status": "external_trust_verified", "algorithm": "Ed25519"}


def verify_trust_set(directory: Path, keys):
    """有界公钥轮换集合；全部不匹配时拒绝，空集合不代表关闭验证。"""
    if (
        not isinstance(keys, list)
        or not 1 <= len(keys) <= 8
        or any(not isinstance(k, str) or not k for k in keys)
    ):
        raise ValueError("信任集合必须包含1至8个公钥路径")
    for key in keys:
        try:
            verify_directory(directory, Path(key))
            return
        except (ValueError, OSError):
            continue
    raise ValueError("模型签名不属于当前信任集合")


def inventory(directory: Path):
    directory = directory.resolve()
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("签名目录不能包含符号链接")
        if path.is_file() and path != directory / SIGNATURE:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            files[path.relative_to(directory).as_posix()] = digest.hexdigest()
    if not files:
        raise ValueError("不能签名空目录")
    return files


def payload(files):
    return json.dumps(
        {"version": 1, "algorithm": "Ed25519", "files": files},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def verify_directory(directory: Path, public_key: Path):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if public_key.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Trust key must be external to the model bundle")
    signature_path = directory / SIGNATURE
    if signature_path.is_symlink() or not signature_path.is_file():
        raise ValueError("缺少模型目录签名")
    try:
        record = json.loads(signature_path.read_text(encoding="utf-8"))
        if record["version"] != 1 or record["algorithm"] != "Ed25519":
            raise ValueError("不支持的签名格式")
        key = load_pem_public_key(public_key.read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("需要Ed25519公钥")
        key.verify(
            base64.b64decode(record["signature"], validate=True),
            payload(record["files"]),
        )
        if inventory(directory) != record["files"]:
            raise ValueError("模型目录文件清单或哈希不匹配")
    except (InvalidSignature, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("模型目录签名无效") from error
    return {"verified": True, "files": len(record["files"]), "algorithm": "Ed25519"}


def sign_directory(directory: Path, private_key: Path):
    """Offline packaging only; reuse the same manifest format as weihu/RUL."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    directory, private_key = Path(directory), Path(private_key)
    if private_key.resolve().is_relative_to(directory.resolve()):
        raise ValueError("私钥必须存放在模型目录外")
    key = load_pem_private_key(private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("需要Ed25519私钥")
    files = inventory(directory)
    record = {"version": 1, "algorithm": "Ed25519", "files": files,
              "signature": base64.b64encode(key.sign(payload(files))).decode("ascii")}
    # Never overwrite an existing signature or follow a signature symlink.
    with (directory / SIGNATURE).open("x", encoding="utf-8") as stream:
        json.dump(record, stream, sort_keys=True, indent=2)
    return {"signed": True, "files": len(files), "algorithm": "Ed25519"}

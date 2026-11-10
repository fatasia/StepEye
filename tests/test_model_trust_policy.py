"""Real Ed25519 policy gates; inference and GPU are not needed."""
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

from visual.model_signature import TRUST_ENV, sign_directory, verify_model_policy, model_trust_keys
from visual.model_release import ModelReleases, validate_release_model
from visual.label_jobs import LabelEngines
from visual.labels import LabelError


@pytest.fixture
def signer(tmp_path, monkeypatch):
    monkeypatch.delenv(TRUST_ENV, raising=False)
    key = Ed25519PrivateKey.generate()
    public, private = tmp_path / "trust.pem", tmp_path / "private.pem"
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))

    def bundle(name, signed=True):
        directory = tmp_path / name
        directory.mkdir()
        model = directory / "model.onnx"
        model.write_bytes(name.encode())
        if signed:
            sign_directory(directory, private)
        return model

    return bundle, public, private


@pytest.mark.parametrize("value", ["", "[]", "null", "{}", '["relative.pem"]'])
def test_configured_bad_policy_never_disables(value, monkeypatch):
    monkeypatch.setenv(TRUST_ENV, value)
    with pytest.raises(ValueError):
        model_trust_keys()


def test_legacy_unsigned_and_strict_signed(signer, monkeypatch):
    bundle, public, _ = signer
    unsigned = bundle("unsigned", False)
    assert verify_model_policy(unsigned)["status"] == "not_required"
    monkeypatch.setenv(TRUST_ENV, json.dumps([str(public)]))
    with pytest.raises(ValueError):
        verify_model_policy(unsigned)
    signed = bundle("signed")
    assert verify_model_policy(signed)["verified"]
    (signed.parent / "classes.json").write_text("[]")
    with pytest.raises(ValueError):
        verify_model_policy(signed)


def test_key_rotation_and_signing_does_not_overwrite(signer, monkeypatch, tmp_path):
    bundle, public, private = signer
    model = bundle("signed")
    monkeypatch.setenv(TRUST_ENV, json.dumps([str(tmp_path / "retired.pem"), str(public)]))
    assert verify_model_policy(model)["verified"]
    with pytest.raises(FileExistsError):
        sign_directory(model.parent, private)
    with pytest.raises(ValueError, match="目录外"):
        sign_directory(model.parent, model.parent / "private.pem")


def candidate(store, model):
    return store.create(engine_id=model.parent.name, model_path=str(model),
                        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                        evaluation_id="HE-fixture", adapter="yolo_raw", classes=["part"], actor="admin")


def test_approval_activation_and_rollback_recheck_without_partial_changes(signer, monkeypatch, tmp_path):
    bundle, public, private = signer
    monkeypatch.setenv(TRUST_ENV, json.dumps([str(public)]))
    store = ModelReleases(tmp_path / "releases.db")
    try:
        first = bundle("first", False)
        a = candidate(store, first)
        with pytest.raises(ValueError):
            store.approve(a["id"], "admin")
        assert store.get(a["id"])["revision"] == 1
        sign_directory(first.parent, private)
        assert store.approve(a["id"], "admin")["signature"]["verified"]
        signature = first.parent / "signature.json"
        saved = signature.read_bytes()
        signature.unlink()
        with pytest.raises(ValueError):
            store.activate(a["id"], "station", "admin")
        assert store.get(a["id"])["status"] == "approved"
        signature.write_bytes(saved)
        store.activate(a["id"], "station", "admin")
        b = candidate(store, bundle("second"))
        store.approve(b["id"], "admin")
        store.activate(b["id"], "station", "admin")
        signature.unlink()
        with pytest.raises(ValueError):
            store.rollback(b["id"], "admin")
        assert store.active_for("station")["id"] == b["id"]
        signature.write_bytes(saved)
        store.rollback(b["id"], "admin")
        assert store.active_for("station")["signature"]["verified"]
        first.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="SHA"):
            validate_release_model(store.get(a["id"]))
    finally:
        store.close()


def test_registry_snapshot_gate(signer, monkeypatch):
    bundle, public, _ = signer
    engines = LabelEngines()
    engines.register("unsigned", bundle("unsigned", False), ["part"])
    engines.register("signed", bundle("signed"), ["part"])
    monkeypatch.setenv(TRUST_ENV, json.dumps([str(public)]))
    with pytest.raises(LabelError) as error:
        engines.snapshot("unsigned")
    assert error.value.status == 409
    assert engines.snapshot("signed")["signature"]["verified"]


def test_runtime_gate_precedes_session_creation(signer, monkeypatch):
    from visual.dsl import DetectorCfg
    from visual.onnx_detector import OnnxObjectDetector
    bundle, public, _ = signer
    model = bundle("unsigned", False)
    monkeypatch.setenv(TRUST_ENV, json.dumps([str(public)]))
    monkeypatch.setattr("visual.detector_registry.create_backend", lambda *a, **k: pytest.fail("session created"))
    with pytest.raises(ValueError, match="签名"):
        OnnxObjectDetector(DetectorCfg(model=str(model)), {})

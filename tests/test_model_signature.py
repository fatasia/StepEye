import base64,json
import pytest
from visual.model_signature import inventory,payload,verify_directory
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat


def test_external_trust_and_tampering(tmp_path):
    bundle=tmp_path/'bundle';bundle.mkdir();(bundle/'model').write_bytes(b'model')
    key=Ed25519PrivateKey.generate();public=tmp_path/'public.pem'
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))
    files=inventory(bundle)
    (bundle/'signature.json').write_text(json.dumps({'version':1,'algorithm':'Ed25519','files':files,'signature':base64.b64encode(key.sign(payload(files))).decode()}))
    assert verify_directory(bundle,public)['verified']
    (bundle/'model').write_bytes(b'tamper')
    with pytest.raises(ValueError):verify_directory(bundle,public)
    with pytest.raises(ValueError):verify_directory(bundle,bundle/'public.pem')


def test_missing_signature_and_empty_trust(tmp_path):
    from visual.model_signature import verify_trust_set
    with pytest.raises(ValueError):verify_trust_set(tmp_path,[])
    with pytest.raises(ValueError):verify_directory(tmp_path,tmp_path.parent/'external.pem')

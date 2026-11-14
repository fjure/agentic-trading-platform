import json
import os
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .custodian import Custodian, CustodyDenied
from .custody import attestation_message


def test_custodian_encrypts_and_denies_replay_after_restore(tmp_path, capsys):
    if os.name != "posix" or os.geteuid() == 0:
        with pytest.raises(CustodyDenied, match="CUSTODY_ISOLATION_REQUIRED"):
            Custodian(
                tmp_path,
                development_uid=1,
                reviewer_uid=2,
                encryption_key_file=tmp_path,
                signer_key_file=tmp_path,
            )
        return

    state, ledger = tmp_path / "state", tmp_path / "ledger"
    state.mkdir(mode=0o700)
    ledger.mkdir(mode=0o700)
    sign = Ed25519PrivateKey.generate()
    for name, key in (("aes", os.urandom(32)), ("sign", sign.private_bytes_raw())):
        path = state / name
        path.touch(mode=0o600)
        path.write_bytes(key)
    options = dict(
        development_uid=os.geteuid() + 1,
        reviewer_uid=os.geteuid() + 2,
        encryption_key_file=state / "aes",
        signer_key_file=state / "sign",
        release_ledger=ledger,
    )
    custodian = Custodian.provision(state, **options)
    row = dict(
        caseId="DE-X",
        version=1,
        split="holdout",
        group="expert",
        cluster="cluster",
        offlineOnly=False,
    )
    holdout = json.dumps(dict(version=1, cases=[row | {"input": "private-canary"}])).encode()
    development = b'{"version":1,"cases":[]}'
    manifest = json.dumps(
        dict(
            version=1,
            cases=[row],
            holdoutSha256=sha256(holdout).hexdigest(),
            developmentSha256=sha256(development).hexdigest(),
        )
    ).encode()
    artifacts = {
        "holdout.v1.json": holdout,
        "split-manifest.v1.json": manifest,
        "development.v1.json": development,
        "primary-review.json": b"{}",
        "secondary-review.json": b"{}",
    }
    with pytest.raises(CustodyDenied):
        custodian.seal("1", artifacts | {"holdout.v1.json": holdout + b" "})
    hashes = custodian.seal("1", artifacts)
    db = next(state.glob("*.sqlite"))
    backup = db.read_bytes()
    assert b"private-canary" not in backup
    assert b"private-canary" not in repr(custodian.audit("1")).encode()
    doc = dict(version=2, holdoutSha256=hashes[0], manifestSha256=hashes[1])
    released = custodian.release("1", verify=lambda sealed: json.dumps(doc).encode())
    assert set(released) == {
        "holdout.v1.json",
        "split-manifest.v1.json",
        "reviewer-attestation.json",
        "reviewer-attestation.sig",
    }
    sign.public_key().verify(released["reviewer-attestation.sig"], attestation_message(doc))
    db.write_bytes(backup)
    with pytest.raises(CustodyDenied, match="CUSTODY_RELEASE_UNAVAILABLE"):
        Custodian(state, **options).release("1", verify=lambda sealed: json.dumps(doc).encode())
    assert "private-canary" not in capsys.readouterr().out

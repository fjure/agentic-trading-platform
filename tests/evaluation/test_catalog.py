"""Validate the public fixture without opening or exposing the held-out texts."""

import pytest

from .catalog import FIXTURES, load_catalog

MANIFEST = FIXTURES / "split-manifest.v1.json"
DEVELOPMENT = FIXTURES / "development.v1.json"


def test_catalog_public_split_and_hash():
    development, holdout = load_catalog(MANIFEST, DEVELOPMENT)
    assert len(development) >= 24
    assert not holdout
    assert all(row["split"] == "development" for row in development)
    assert len({row["cluster"] for row in development}) == len(development)


def test_catalog_tampering_blocks_loading(tmp_path):
    path = tmp_path / "development.v1.json"
    path.write_bytes(DEVELOPMENT.read_bytes() + b" ")
    with pytest.raises(ValueError, match="EVALUATION_DATASET_HASH"):
        load_catalog(MANIFEST, path)

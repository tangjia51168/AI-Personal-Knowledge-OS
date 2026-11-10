import hashlib

import pytest

from scripts import prepare_minio_mc as prepare


def test_mc_verification_rejects_missing_truncated_or_corrupted_file(tmp_path, monkeypatch):
    expected = b"official-mc-fixture"
    monkeypatch.setattr(prepare, "SIZE", len(expected))
    monkeypatch.setattr(prepare, "SHA256", hashlib.sha256(expected).hexdigest())
    artifact = tmp_path / "mc"
    assert not prepare.verified(artifact)
    artifact.write_bytes(expected[:-1])
    assert not prepare.verified(artifact)
    artifact.write_bytes(b"x" * len(expected))
    assert not prepare.verified(artifact)
    artifact.write_bytes(expected)
    assert prepare.verified(artifact)
    artifact.rename(tmp_path / "verified")


def test_metadata_requires_exact_official_asset_size_and_digest():
    item = {
        "name": prepare.NAME,
        "url": prepare.ASSET_URL,
        "size": prepare.SIZE,
        "digest": f"sha256:{prepare.SHA256}",
    }
    prepare.validate_metadata({"assets": [item]})
    for field, value in (
        ("url", "https://example.invalid/mc"),
        ("size", 1),
        ("digest", "sha256:wrong"),
    ):
        with pytest.raises(RuntimeError, match="differs"):
            prepare.validate_metadata({"assets": [{**item, field: value}]})
    with pytest.raises(RuntimeError, match="ambiguous"):
        prepare.validate_metadata({"assets": [item, item]})


@pytest.mark.parametrize(
    "location",
    [
        "http://release-assets.githubusercontent.com/file",
        "https://release-assets.githubusercontent.com.example.invalid/file",
        "https://user@release-assets.githubusercontent.com/file",
        "https://release-assets.githubusercontent.com:444/file",
        "https://release-assets.githubusercontent.com/file#fragment",
        "",
    ],
)
def test_redirect_requires_https_official_release_host(location):
    with pytest.raises(RuntimeError):
        prepare.validate_redirect(location)


def test_verified_cache_is_reused_without_network(tmp_path, monkeypatch):
    expected = b"verified-cache"
    monkeypatch.setattr(prepare, "CONTEXT", tmp_path)
    monkeypatch.setattr(prepare, "SIZE", len(expected))
    monkeypatch.setattr(prepare, "SHA256", hashlib.sha256(expected).hexdigest())
    (tmp_path / "mc").write_bytes(expected)
    monkeypatch.setattr(
        prepare.httpx,
        "Client",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network must not be used")),
    )
    assert prepare.prepare() == tmp_path / "mc"

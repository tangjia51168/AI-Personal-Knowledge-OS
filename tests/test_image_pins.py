from scripts import check_image_pins


def test_runtime_images_are_pinned_to_reviewed_digests():
    checks = check_image_pins.check()
    assert checks and all(checks.values())


def test_expected_digest_values_are_full_sha256_strings():
    assert set(check_image_pins.EXPECTED) == {"python", "node", "pgvector", "redis", "nginx"}
    assert all(len(value) == 64 for value in check_image_pins.EXPECTED.values())


def test_mutable_pgvector_reference_cannot_pass_the_guard(monkeypatch, tmp_path):
    for name in (
        "infrastructure/docker/python.Dockerfile",
        "infrastructure/docker/web.Dockerfile",
        "infrastructure/docker/minio.Dockerfile",
        "infrastructure/docker/gateway.Dockerfile",
        "docker-compose.yml",
        "docker-compose.gateway.yml",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((check_image_pins.ROOT / name).read_text(encoding="utf-8"))
    compose = tmp_path / "docker-compose.yml"
    # Keeping the valid reference and introducing a mutable one must still fail.
    with compose.open("a", encoding="utf-8") as stream:
        stream.write("\nimage: pgvector/pgvector:pg16\n")
    monkeypatch.setattr(check_image_pins, "ROOT", tmp_path)
    assert check_image_pins.check()["no_mutable_runtime_base"] is False

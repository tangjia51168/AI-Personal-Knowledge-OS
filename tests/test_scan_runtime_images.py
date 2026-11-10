"""Offline safety and evidence tests; these do not count as a real CVE scan."""

import hashlib
import json
import subprocess

import pytest

from scripts import scan_runtime_images as scan

IMAGE_A = "sha256:" + "a" * 64
IMAGE_B = "sha256:" + "b" * 64
IMAGE_C = "sha256:" + "c" * 64


def sarif(level="warning", fixed="26.2.0"):
    results = [] if level is None else [{"ruleId": "CVE-2026-12345", "level": level}]
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "docker scout",
                        "version": "1.27.0",
                        "rules": [
                            {
                                "id": "CVE-2026-12345",
                                "properties": {
                                    "purls": ["pkg:pypi/pip@25.0.1"],
                                    "fixed_version": fixed,
                                },
                            }
                        ],
                    }
                },
                "results": results,
            }
        ],
    }


def save_sarif(tmp_path, value):
    path = tmp_path / "result.sarif.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize("level", ["error", "warning", "note", "none", None])
def test_sarif_gate_keeps_unfixed_and_notes_and_binds_report_hash(tmp_path, level):
    path = save_sarif(tmp_path, sarif(level, "not fixed"))
    result = scan.sarif_summary(path)
    assert result["status"] == (
        "failed_vulnerability_gate" if level in {"error", "warning"} else "passed"
    )
    assert result["report_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["scanner_versions"] == ["1.27.0"]
    assert result["results"] == (0 if level is None else 1)
    if level is not None:
        assert result["findings"][0]["fixed_version"] == "not fixed"


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"version": "2.1.0", "runs": []},
        {"version": "2.1.0", "runs": [{}]},
    ],
)
def test_sarif_incomplete_structure_cannot_pass(tmp_path, invalid):
    with pytest.raises(scan.ScanFailure):
        scan.sarif_summary(save_sarif(tmp_path, invalid))


@pytest.mark.parametrize(
    "mutation", ["missing_results", "unknown_rule", "bad_level", "bad_purl", "other_scanner"]
)
def test_sarif_malformed_findings_cannot_be_silently_dropped(tmp_path, mutation):
    data = sarif()
    run = data["runs"][0]
    if mutation == "missing_results":
        del run["results"]
    elif mutation == "unknown_rule":
        run["results"][0]["ruleId"] = "CVE-2026-54321"
    elif mutation == "bad_level":
        run["results"][0]["level"] = "safe"
    elif mutation == "bad_purl":
        run["tool"]["driver"]["rules"][0]["properties"]["purls"] = []
    else:
        run["tool"]["driver"]["name"] = "untrusted"
    with pytest.raises(scan.ScanFailure):
        scan.sarif_summary(save_sarif(tmp_path, data))


def test_sarif_missing_default_level_is_warning(tmp_path):
    data = sarif()
    del data["runs"][0]["results"][0]["level"]
    assert scan.sarif_summary(save_sarif(tmp_path, data))["status"] == "failed_vulnerability_gate"


def test_invalid_json_and_missing_report_fail_closed(tmp_path):
    path = tmp_path / "missing.json"
    with pytest.raises(scan.ScanFailure, match="SARIF_MISSING"):
        scan.sarif_summary(path)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(scan.ScanFailure, match="INVALID_JSON"):
        scan.sarif_summary(path)


def containers():
    return [
        {
            "container_id": format(index, "064x"),
            "service": service,
            "name": f"{scan.PROJECT}-{service}-1",
            "image_id": IMAGE_A if index < 4 else IMAGE_B,
            "running": True,
        }
        for index, service in enumerate(scan.REQUIRED_SERVICES, 1)
    ]


def test_inventory_requests_only_selected_fields_and_filters_project(monkeypatch):
    rows = containers()
    rows.append(
        {
            "container_id": "f" * 64,
            "service": "embedding",
            "name": f"{scan.PROJECT}-embedding-1",
            "image_id": IMAGE_C,
            "running": True,
        }
    )
    for index, (service, name) in enumerate(
        [
            ("acceptance", "knowledge-os-track-tests-" + "a" * 12),
            ("acceptance", "knowledge-os-semantic-test-" + "b" * 12),
            ("api", "kos-recovery-" + "c" * 32 + "-api"),
        ]
    ):
        rows.append(
            {
                "container_id": str(index + 1) * 64,
                "service": service,
                "name": name,
                "image_id": IMAGE_C,
                "running": False,
            }
        )
    raw_rows = {
        row["container_id"]: {
            "id": row["container_id"],
            "name": "/" + row["name"],
            "project": scan.PROJECT,
            "service": row["service"],
            "image_id": row["image_id"],
            "running": row["running"],
            "unexpected_secret": "not-forwarded",
        }
        for row in rows
    }
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == "ps":
            return "\n".join(raw_rows).encode()
        return json.dumps(raw_rows[args[-1]]).encode()

    monkeypatch.setattr(scan, "run_docker", run)
    assert scan.inventory() == sorted(rows, key=lambda row: row["container_id"])
    assert f"label=com.docker.compose.project={scan.PROJECT}" in calls[0]
    assert all(call[2] == scan.CONTAINER_FORMAT for call in calls[1:])
    assert ".Config.Env" not in scan.CONTAINER_FORMAT
    assert "not-forwarded" not in json.dumps(scan.inventory())


@pytest.mark.parametrize(
    "name,service",
    [
        ("knowledge-os-track-tests-" + "a" * 12, "api"),
        ("kos-recovery-" + "b" * 32 + "-api", "worker"),
        ("kos-recovery-anything-api", "api"),
        ("production-api", "api"),
    ],
)
def test_helper_names_require_exact_service_and_uuid(name, service):
    assert not scan.allowed_container_name(name, service)


@pytest.mark.parametrize("invalid", [b"", b"not-an-id", b"a" * 64 + b"\n" + b"a" * 64])
def test_empty_and_invalid_container_inventory_rejected(monkeypatch, invalid):
    monkeypatch.setattr(scan, "run_docker", lambda *args, **kwargs: invalid)
    with pytest.raises(scan.ScanFailure):
        scan.inventory()


@pytest.mark.parametrize(
    "field,value",
    [
        ("project", "production"),
        ("service", "unexpected"),
        ("name", "/other-container"),
        ("image_id", "mutable:latest"),
        ("running", "true"),
    ],
)
def test_foreign_or_malformed_container_metadata_rejected(monkeypatch, field, value):
    row = containers()[0]
    payload = {
        "id": row["container_id"],
        "name": row["name"],
        "project": scan.PROJECT,
        "service": row["service"],
        "image_id": IMAGE_A,
        "running": True,
        field: value,
    }
    monkeypatch.setattr(
        scan,
        "run_docker",
        lambda *args, **kwargs: (
            row["container_id"].encode() if args[0] == "ps" else json.dumps(payload).encode()
        ),
    )
    with pytest.raises(scan.ScanFailure, match="INVALID_CONTAINER_INVENTORY"):
        scan.inventory()


@pytest.mark.parametrize(
    "metadata",
    [
        {"image_id": IMAGE_B, "repo_digests": []},
        {"image_id": IMAGE_A, "repo_digests": ["https://user:password@example.invalid"]},
    ],
)
def test_image_mismatch_and_unsafe_repository_reference_cannot_enter_report(monkeypatch, metadata):
    monkeypatch.setattr(scan, "run_docker", lambda *args, **kwargs: json.dumps(metadata).encode())
    with pytest.raises(scan.ScanFailure):
        scan.image_metadata(IMAGE_A)


def test_missing_runtime_service_fails_inventory(monkeypatch):
    row = containers()[0]
    payload = {
        "id": row["container_id"],
        "name": row["name"],
        "project": scan.PROJECT,
        "service": row["service"],
        "image_id": IMAGE_A,
        "running": True,
    }
    monkeypatch.setattr(
        scan,
        "run_docker",
        lambda *args, **kwargs: (
            row["container_id"].encode() if args[0] == "ps" else json.dumps(payload).encode()
        ),
    )
    with pytest.raises(scan.ScanFailure, match="REQUIRED_RUNTIME_SERVICES_MISSING"):
        scan.inventory()


def setup_scan(monkeypatch, tmp_path, *, drift=False, command_failure=False, malformed=False):
    monkeypatch.setattr(scan, "REPORT_ROOT", tmp_path)
    rows = containers()
    snapshots = iter([rows, rows[:-1] if drift else rows])
    monkeypatch.setattr(scan, "inventory", lambda: next(snapshots))
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "image":
            return json.dumps({"image_id": args[-1], "repo_digests": []}).encode()
        if command_failure:
            raise RuntimeError("redis://user:secret@somewhere.invalid:6379/0")
        report = args[args.index("--output") + 1]
        from pathlib import Path

        Path(report).write_text("bad" if malformed else json.dumps(sarif()), encoding="utf-8")
        return b"ignored potentially sensitive CLI diagnostic"

    monkeypatch.setattr(scan, "run_docker", docker)
    return calls


def test_scan_deduplicates_actual_ids_maps_all_services_and_never_pulls(monkeypatch, tmp_path):
    calls = setup_scan(monkeypatch, tmp_path)
    path = scan.scan_acceptance()
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == "failed_vulnerability_gate"
    assert report["running_runtime_gate"] == "failed_vulnerability_gate"
    assert report["all_inventoried_images_gate"] == report["status"]
    assert report["inventory_stable"] is True
    assert len(report["images"]) == 2
    assert report["scanner_versions"] == ["1.27.0"]
    assert {service for item in report["images"] for service in item["services"]} == set(
        scan.REQUIRED_SERVICES
    )
    assert all(item["running_services"] == item["services"] for item in report["images"])
    assert all(item["exited_services"] == [] for item in report["images"])
    scan_calls = [call for call in calls if call[0] == "scout"]
    assert len(scan_calls) == 2
    assert {call[2] for call in scan_calls} == {"local://" + IMAGE_A, "local://" + IMAGE_B}
    assert all("--ignore-base" not in call and "--only-fixed" not in call for call in scan_calls)
    assert not any(call[0] in {"push", "pull", "login"} for call in calls)
    assert all((path.parent / item["report"]).is_file() for item in report["images"])
    assert report["max_parallel_scans"] == 3


def test_parallel_workers_overlap_but_never_exceed_three(monkeypatch, tmp_path):
    import threading

    setup_scan(monkeypatch, tmp_path)
    rows = containers() + [
        {
            "container_id": str(index) * 64,
            "service": "acceptance",
            "name": f"{scan.PROJECT}-tests-parallel-{index}",
            "image_id": "sha256:" + character * 64,
            "running": False,
        }
        for index, character in ((8, "d"), (9, "e"))
    ]
    monkeypatch.setattr(scan, "inventory", lambda: rows)
    original = scan.run_docker
    rendezvous = threading.Barrier(3)
    lock = threading.Lock()
    active = 0
    maximum = 0
    calls = 0

    def concurrent_docker(*args, **kwargs):
        nonlocal active, maximum, calls
        if args[0] != "scout":
            return original(*args, **kwargs)
        with lock:
            active += 1
            maximum = max(maximum, active)
            calls += 1
            ordinal = calls
        try:
            if ordinal <= 3:
                rendezvous.wait(timeout=5)
            return original(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(scan, "run_docker", concurrent_docker)
    report = json.loads(scan.scan_acceptance().read_text("utf-8"))
    assert maximum == 3 and active == 0
    assert report["inventory_stable"] is True
    assert len(report["images"]) == 4


@pytest.mark.parametrize(
    "options",
    [
        {"drift": True},
        {"command_failure": True},
        {"malformed": True},
    ],
)
def test_drift_and_failed_scan_cannot_pass_and_do_not_leak_errors(monkeypatch, tmp_path, options):
    setup_scan(monkeypatch, tmp_path, **options)
    path = scan.scan_acceptance()
    raw = path.read_text(encoding="utf-8")
    report = json.loads(raw)
    assert report["status"] == "failed_scan"
    assert report["running_runtime_gate"] == "failed_scan"
    assert report["all_inventoried_images_gate"] == "failed_scan"
    assert "secret" not in raw and "somewhere.invalid" not in raw
    if options.get("drift"):
        assert report["errors"] == ["CONTAINER_INVENTORY_CHANGED"]


def test_initial_inventory_failure_is_reported_without_raw_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "REPORT_ROOT", tmp_path)
    monkeypatch.setattr(scan, "inventory", lambda: (_ for _ in ()).throw(RuntimeError("secret")))
    report = json.loads(scan.scan_acceptance().read_text(encoding="utf-8"))
    assert report["status"] == "failed_scan"
    assert report["running_runtime_gate"] == "failed_scan"
    assert report["all_inventoried_images_gate"] == "failed_scan"
    assert report["errors"] == ["SCAN_FAILED"]
    assert report["images"] == []


def test_complete_clean_scan_can_pass_and_new_run_preserves_old_report(monkeypatch, tmp_path):
    setup_scan(monkeypatch, tmp_path)
    original = scan.run_docker

    def clean_docker(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "scout":
            from pathlib import Path

            Path(args[args.index("--output") + 1]).write_text(
                json.dumps(sarif(None)), encoding="utf-8"
            )
        return result

    monkeypatch.setattr(scan, "run_docker", clean_docker)
    first = scan.scan_acceptance()
    raw = first.read_bytes()
    assert json.loads(raw)["status"] == "passed"
    assert json.loads(raw)["running_runtime_gate"] == "passed"
    assert json.loads(raw)["all_inventoried_images_gate"] == "passed"
    monkeypatch.setattr(scan, "inventory", containers)
    second = scan.scan_acceptance()
    assert second.parent != first.parent
    assert first.read_bytes() == raw


@pytest.mark.parametrize("historical_failure", [False, True])
def test_exited_old_test_image_stays_visible_without_confusing_runtime_scope(
    monkeypatch, tmp_path, historical_failure
):
    setup_scan(monkeypatch, tmp_path)
    rows = containers() + [
        {
            "container_id": "f" * 64,
            "service": "acceptance",
            "name": f"{scan.PROJECT}-tests-old",
            "image_id": IMAGE_C,
            "running": False,
        }
    ]
    monkeypatch.setattr(scan, "inventory", lambda: rows)
    original = scan.run_docker

    def scoped_docker(*args, **kwargs):
        if args[0] == "scout" and args[2] == "local://" + IMAGE_C and historical_failure:
            raise scan.ScanFailure("DOCKER_COMMAND_FAILED")
        result = original(*args, **kwargs)
        if args[0] == "scout" and args[2] != "local://" + IMAGE_C:
            from pathlib import Path

            Path(args[args.index("--output") + 1]).write_text(
                json.dumps(sarif(None)), encoding="utf-8"
            )
        return result

    monkeypatch.setattr(scan, "run_docker", scoped_docker)
    report = json.loads(scan.scan_acceptance().read_text(encoding="utf-8"))
    old = next(item for item in report["images"] if item["image_id"] == IMAGE_C)
    assert old["running_services"] == []
    assert old["exited_services"] == ["acceptance"]
    assert old["services"] == ["acceptance"]
    assert report["running_runtime_gate"] == ("failed_scan" if historical_failure else "passed")
    assert report["all_inventoried_images_gate"] == (
        "failed_scan" if historical_failure else "failed_vulnerability_gate"
    )
    assert report["status"] == report["all_inventoried_images_gate"]


def test_even_clean_scan_requires_every_required_running_service(monkeypatch, tmp_path):
    setup_scan(monkeypatch, tmp_path)
    rows = containers()
    rows[-1]["running"] = False
    monkeypatch.setattr(scan, "inventory", lambda: rows)
    original = scan.run_docker

    def clean_docker(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "scout":
            from pathlib import Path

            Path(args[args.index("--output") + 1]).write_text(
                json.dumps(sarif(None)), encoding="utf-8"
            )
        return result

    monkeypatch.setattr(scan, "run_docker", clean_docker)
    report = json.loads(scan.scan_acceptance().read_text(encoding="utf-8"))
    assert report["running_runtime_gate"] == "failed_scan"


def test_docker_stderr_and_timeout_are_sanitized(monkeypatch):
    monkeypatch.setattr(scan, "docker_path", lambda: "docker")
    monkeypatch.setattr(
        scan.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"secret"),
    )
    with pytest.raises(scan.ScanFailure, match="^DOCKER_COMMAND_FAILED$"):
        scan.run_docker("ps")
    monkeypatch.setattr(
        scan.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("secret", 1)),
    )
    with pytest.raises(scan.ScanFailure, match="^DOCKER_COMMAND_FAILED$"):
        scan.run_docker("ps")


@pytest.mark.parametrize("arguments", [[], ["--acceptance"], ["--allow-metadata-egress"]])
def test_both_opt_ins_required_before_any_docker_action(monkeypatch, arguments):
    monkeypatch.setattr("sys.argv", ["scan", *arguments])
    monkeypatch.setattr(scan, "scan_acceptance", lambda: pytest.fail("unauthorized scan"))
    with pytest.raises(SystemExit) as exc:
        scan.main()
    assert exc.value.code == 2

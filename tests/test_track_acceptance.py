"""Acceptance reporting must not turn empty or partial coverage into completion."""

import json
from types import SimpleNamespace

import pytest

from scripts import track_acceptance as check


def test_track_suites_only_reference_existing_fixed_tests():
    assert set(check.SUITES) == {
        "foundation",
        "sources",
        "understanding",
        "evidence",
        "organization",
        "notes",
        "learning",
        "rag",
        "sync",
        "console",
        "local-ai",
        "release",
        "collections",
    }
    for track in check.SUITES:
        record = check.plan(track)
        assert record["overall_acceptance"] == "not_accepted"
        for files in record["stages"].values():
            assert len(files) == len(set(files))
            assert all((check.ROOT / path).is_file() for path in files)
        assert not any("acceptance.py" in path for path in record["stages"]["integration"])


def test_empty_rag_suite_is_unverified_with_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setitem(check.SUITES, "rag", {"integration": [], "browser": []})
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(
        check.sys, "argv", ["track_acceptance", "--track", "rag", "--run", "integration"]
    )
    assert check.main() == 1
    record = json.loads(next(tmp_path.rglob("summary.json")).read_text())
    assert record["status"] == "unverified"
    assert record["overall_acceptance"] == "not_accepted"


def test_failed_stage_sanitizes_exception_and_never_marks_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(
        check.sys, "argv", ["track_acceptance", "--track", "organization", "--run", "integration"]
    )

    def fail(*args):
        raise RuntimeError("secret connection string")

    monkeypatch.setattr(check, "integration", fail)
    assert check.main() == 1
    text = next(tmp_path.rglob("summary.json")).read_text()
    assert "secret" not in text
    assert json.loads(text)["status"] == "failed"


def test_list_plan_does_not_contact_services(monkeypatch, capsys):
    monkeypatch.setattr(
        check.sys, "argv", ["track_acceptance", "--track", "organization", "--list"]
    )
    monkeypatch.setattr(check, "integration", lambda *args: pytest.fail("Plan contacted a service"))
    assert check.main() == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "implemented_scope_only"


@pytest.mark.parametrize(
    "patch", [{"skipped": 1}, {"unexpected": 1}, {"flaky": 1}, {"expected": 0}, {"expected": True}]
)
def test_browser_report_rejects_empty_skipped_failed_flaky_or_invalid_counts(tmp_path, patch):
    report = tmp_path / "browser.json"
    report.write_text(
        json.dumps({"stats": {"expected": 13, "skipped": 0, "unexpected": 0, "flaky": 0, **patch}})
    )
    with pytest.raises(RuntimeError):
        check.verify_browser_report(report)


def test_browser_report_accepts_only_complete_test_counts(tmp_path):
    report = tmp_path / "browser.json"
    stats = {"expected": 13, "skipped": 0, "unexpected": 0, "flaky": 0}
    report.write_text(json.dumps({"stats": stats}))
    assert check.verify_browser_report(report) == stats


def test_browser_isolation_rejects_other_projects_before_creating_container(monkeypatch, tmp_path):
    from scripts import operations_check

    calls = []
    monkeypatch.setattr(operations_check, "docker_path", lambda: "docker")
    record = {
        "Config": {"Labels": {"com.docker.compose.project": "user-project"}},
        "NetworkSettings": {"Networks": {}},
        "Image": "sha256:" + "a" * 64,
    }
    monkeypatch.setattr(
        check.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps([record]).encode()),
    )
    monkeypatch.setattr(check, "execute", lambda *args: calls.append(args))
    with pytest.raises(RuntimeError, match="Unexpected acceptance Web identity"):
        with check.isolated_browser_web(tmp_path):
            pytest.fail("Unsafe Web was accepted")
    assert calls == []


def test_browser_isolation_stops_only_own_container_when_binding_is_invalid(monkeypatch, tmp_path):
    from scripts import operations_check

    calls = []
    monkeypatch.setattr(operations_check, "docker_path", lambda: "docker")
    record = {
        "Config": {
            "Labels": {
                "com.docker.compose.project": "knowledge-os-acceptance",
                "com.docker.compose.service": "web",
            }
        },
        "NetworkSettings": {"Networks": {"knowledge-os-acceptance_default": {}}},
        "Image": "sha256:" + "a" * 64,
    }

    def run(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([record]).encode() if command[1] == "inspect" else b"0.0.0.0:3000",
        )

    monkeypatch.setattr(check.subprocess, "run", run)
    monkeypatch.setattr(check, "execute", lambda command, log: calls.append(command))
    with pytest.raises(RuntimeError, match="Unexpected browser Web binding"):
        with check.isolated_browser_web(tmp_path):
            pytest.fail("Public binding was accepted")
    assert calls[0][1] == "run"
    assert calls[-1][:4] == ["docker", "stop", "--time", "10"]
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[-1][-1] == name
    assert name.startswith("knowledge-os-track-web-")
    assert "--volume" not in calls[0] and "--mount" not in calls[0]


def test_browser_assigns_distinct_origins_for_organization_cases_and_cleans_both(
    monkeypatch, tmp_path
):
    from contextlib import contextmanager

    from scripts import operations_check

    active, closed, calls = [], [], []
    monkeypatch.setattr(operations_check, "load_acceptance", lambda: {})
    monkeypatch.setattr(operations_check, "load_runtime_storage", lambda _values: {})

    @contextmanager
    def isolated(directory):
        origin = f"http://127.0.0.1:{13000 + len(active)}"
        active.append(origin)
        try:
            yield origin
        finally:
            closed.append(origin)

    def execute(command, _log, environment):
        calls.append(command)
        assert environment["E2E_BASE_URL"] != environment["E2E_HIERARCHY_BASE_URL"]
        assert len(active) == 2
        raise RuntimeError("controlled browser failure")

    monkeypatch.setattr(check, "isolated_browser_web", isolated)
    monkeypatch.setattr(check, "execute", execute)
    with pytest.raises(RuntimeError, match="controlled browser failure"):
        check.browser("organization", tmp_path)
    assert len(calls) == 1
    assert "topic-hierarchy.spec.ts" in calls[0] and "note-labels.spec.ts" in calls[0]
    assert closed == list(reversed(active))


def test_browser_network_ip_does_not_reuse_active_prior_or_gateway_address(monkeypatch, tmp_path):
    from scripts import operations_check

    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(operations_check, "docker_path", lambda: "docker")
    prior = tmp_path / ".cache/track-acceptance/old/topic-web"
    prior.mkdir(parents=True)
    (prior / "browser-web.json").write_text(json.dumps({"container_ip": "172.30.0.3"}))
    record = {
        "Config": {
            "Labels": {
                "com.docker.compose.project": "knowledge-os-acceptance",
                "com.docker.compose.service": "web",
            }
        },
        "NetworkSettings": {"Networks": {"knowledge-os-acceptance_default": {}}},
        "Image": "sha256:" + "a" * 64,
    }
    network = {
        "IPAM": {"Config": [{"Subnet": "172.30.0.0/29", "Gateway": "172.30.0.1"}]},
        "Containers": {"web": {"IPv4Address": "172.30.0.2/29"}},
    }
    calls = []

    def run(command, **_kwargs):
        output = json.dumps([record if command[1] == "inspect" else network]).encode()
        if command[1] == "port":
            output = b"0.0.0.0:3000"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(check.subprocess, "run", run)
    monkeypatch.setattr(check, "execute", lambda command, log: calls.append(command))
    with pytest.raises(RuntimeError, match="Unexpected browser Web binding"):
        with check.isolated_browser_web(tmp_path):
            pytest.fail("Invalid public port was accepted")
    selected = calls[0][calls[0].index("--ip") + 1]
    assert selected in {"172.30.0.4", "172.30.0.5", "172.30.0.6"}
    assert calls[-1][-1] == calls[0][calls[0].index("--name") + 1]

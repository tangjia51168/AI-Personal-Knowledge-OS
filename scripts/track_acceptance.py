"""Run explicit, bounded acceptance suites for implemented track scopes.

Reports never mark the full product track complete. No model/platform acceptance
scripts, deployment, service restart or migration are dispatched by this command.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
# Suites validate only currently implemented scopes, never the future goals.
SUITES: dict[str, dict[str, list[str]]] = {
    "collections": {
        "integration": ["test_collections_integration"],
        "browser": ["collections.spec.ts"],
    },
    "foundation": {
        "integration": [
            "test_stack_integration",
            "test_runtime_database_role_integration",
            "test_tenant_rls_integration",
            "test_jobs_integration",
            "test_metrics_integration",
        ],
        "browser": ["project-progress.spec.ts"],
    },
    "sources": {
        "integration": [
            "test_sources_integration",
            "test_collection_integration",
            "test_source_import_integration",
        ],
        "browser": ["content-filters.spec.ts"],
    },
    "understanding": {
        "integration": ["test_notes_integration"],
        "browser": ["knowledge.spec.ts", "long-analysis.spec.ts"],
    },
    "evidence": {
        "integration": [
            "test_notes_integration",
            "test_source_import_integration",
            "test_manual_first_note_integration",
        ],
        "browser": ["knowledge.spec.ts", "knowledge-explorer.spec.ts"],
    },
    "organization": {
        "integration": [
            "test_organization_integration",
            "test_topic_hierarchy_integration",
            "test_concept_cards_integration",
            "test_note_links_integration",
            "test_note_graph_integration",
            "test_annotations_integration",
            "test_knowledge_explorer_integration",
            "test_tenant_rls_integration",
        ],
        "browser": [
            "knowledge-explorer.spec.ts",
            "note-labels.spec.ts",
            "topic-hierarchy.spec.ts",
            "concept-cards.spec.ts",
            "note-links.spec.ts",
            "note-graph.spec.ts",
            "note-annotations.spec.ts",
            "content-filters.spec.ts",
            "project-progress.spec.ts",
        ],
    },
    "notes": {
        "integration": [
            "test_notes_integration",
            "test_annotations_integration",
            "test_note_exports_integration",
            "test_manual_first_note_integration",
        ],
        "browser": ["knowledge.spec.ts", "note-annotations.spec.ts"],
    },
    "learning": {
        "integration": [
            "test_learning_integration",
            "test_learning_summary_integration",
            "test_learning_analysis_integration",
            "test_learning_schedule_integration",
            "test_learning_filters_integration",
            "test_reminder_batch_integration",
            "test_notifications_integration",
        ],
        "browser": ["knowledge.spec.ts", "notifications.spec.ts"],
    },
    "rag": {
        "integration": ["test_research_answers_integration"],
        "browser": ["product-workspace.spec.ts"],
    },
    "sync": {
        "integration": ["test_jobs_integration", "test_source_schedules_integration"],
        "browser": ["product-workspace.spec.ts"],
    },
    "console": {
        "integration": ["test_manual_first_note_integration"],
        "browser": [
            "dashboard.spec.ts",
            "project-progress.spec.ts",
            "knowledge.spec.ts",
            "knowledge-explorer.spec.ts",
            "product-workspace.spec.ts",
            "long-analysis.spec.ts",
        ],
    },
    "local-ai": {
        "integration": [
            "test_ai_settings_integration",
            "test_ai_routing_integration",
            "test_ai_provider_updates_integration",
            "test_ai_transport_integration",
        ],
        "browser": [],
    },
    "release": {
        "integration": [
            "test_provision_integration",
            "test_identity_integration",
            "test_tenant_rls_integration",
            "test_files_integration",
            "test_audit_integration",
        ],
        "browser": [
            "account.spec.ts",
            "audit.spec.ts",
            "files.spec.ts",
            "jobs.spec.ts",
            "models.spec.ts",
            "security-events.spec.ts",
            "imports.spec.ts",
            "note-edits.spec.ts",
            "sources.spec.ts",
            "project-progress.spec.ts",
        ],
    },
}


def suite_files(track: str, stage: str) -> list[str]:
    if stage == "static":
        return ["scripts/check.ps1"]
    if stage == "integration":
        return [f"tests/{name}.py" for name in SUITES[track][stage]]
    return [f"apps/web/tests/{name}" for name in SUITES[track][stage]]


def plan(track: str) -> dict:
    return {
        "track": track,
        "scope": "implemented_scope_only",
        "overall_acceptance": "not_accepted",
        "requirements": "docs/mainline-acceptance.md",
        "stages": {
            stage: suite_files(track, stage) for stage in ("static", "integration", "browser")
        },
        "limitations": [
            "Empty suites are unverified, never a passing result.",
            "Protocol fixtures do not validate external platforms or actual model inference.",
            "Deferred goals and production gates remain open after any passing suite.",
        ],
    }


def execute(command: list[str], log: Path, environment: dict[str, str] | None = None) -> None:
    # Child output stays in the local report, never interpolate credentials into commands.
    with log.open("ab") as stream:
        result = subprocess.run(
            command, cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT, check=False
        )
    if result.returncode:
        raise RuntimeError("Acceptance stage failed; inspect the local report")


def verify_browser_report(path: Path) -> dict[str, int]:
    stats = json.loads(path.read_text(encoding="utf-8"))["stats"]
    names = ("expected", "unexpected", "flaky", "skipped")
    if any(type(stats.get(key)) is not int or stats[key] < 0 for key in names):
        raise RuntimeError("Invalid browser report")
    if stats["expected"] < 1 or any(stats[key] for key in names[1:]):
        raise RuntimeError("Incomplete browser acceptance")
    return {key: stats[key] for key in names}


def integration(track: str, directory: Path) -> None:
    from xml.etree import ElementTree as ET

    from scripts.operations_check import compose_args, docker_path, load_acceptance

    # Check the configured local endpoints before dispatching any write-capable tests.
    values = load_acceptance()
    compose = compose_args(values)
    compose[3:3] = ["--env-file", str(ROOT / ".env.storage-runtime.acceptance")]
    compose += [
        "-f",
        str(ROOT / "docker-compose.storage-runtime.yml"),
        "-f",
        str(ROOT / "docker-compose.hardened.yml"),
    ]
    docker = docker_path()
    name = "knowledge-os-track-tests-" + uuid4().hex[:12]
    log = directory / "integration.log"
    # Use the existing test image and live isolated services, not a fresh empty DB.
    failure = None
    try:
        execute(
            [
                docker,
                *compose,
                "--profile",
                "acceptance",
                "run",
                "--no-deps",
                "--name",
                name,
                "acceptance",
                "python",
                "-m",
                "pytest",
                "-m",
                "integration",
                "-q",
                "-o",
                "cache_dir=/tmp/pytest-cache",
                "--junitxml=/tmp/track.xml",
                *suite_files(track, "integration"),
            ],
            log,
        )
    except RuntimeError as error:
        failure = error
    # Collect even a failed test report; retain the failed container for diagnosis.
    execute([docker, "cp", f"{name}:/tmp/track.xml", str(directory / "integration.xml")], log)
    report = ET.parse(directory / "integration.xml").getroot()
    suites = [report] if report.tag == "testsuite" else list(report.findall("testsuite"))
    counts = {
        key: sum(int(suite.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if counts["tests"] < 1 or any(counts[key] for key in ("failures", "errors", "skipped")):
        raise RuntimeError("Incomplete integration report; test container retained")
    (directory / "integration-summary.json").write_text(
        json.dumps(counts, indent=2), encoding="utf-8"
    )
    if failure:
        raise failure
    # Only this successfully completed test container is removed. Volumes are untouched.
    execute([docker, "rm", name], log)


@contextlib.contextmanager
def isolated_browser_web(directory: Path):
    """Fresh Web source IP; do not erase shared Redis or relax product limits."""
    import httpx

    from scripts.operations_check import docker_path

    docker = docker_path()
    inspected = subprocess.run(
        [docker, "inspect", "knowledge-os-acceptance-web-1"],
        capture_output=True,
        check=False,
        timeout=15,
    )
    if inspected.returncode:
        raise RuntimeError("Acceptance Web is unavailable")
    original = json.loads(inspected.stdout)[0]
    labels = original["Config"].get("Labels") or {}
    network = "knowledge-os-acceptance_default"
    image = original["Image"]
    if (
        labels.get("com.docker.compose.project") != "knowledge-os-acceptance"
        or labels.get("com.docker.compose.service") != "web"
        or network not in original["NetworkSettings"]["Networks"]
        or not image.startswith("sha256:")
    ):
        raise RuntimeError("Unexpected acceptance Web identity")
    name = "knowledge-os-track-web-" + uuid4().hex[:12]
    log = directory / "browser-web.log"
    ip_args: list[str] = []
    network_details = subprocess.run(
        [docker, "network", "inspect", network], capture_output=True, check=False, timeout=15
    )
    if network_details.returncode == 0:
        try:
            network_record = json.loads(network_details.stdout)[0]
            subnet = ipaddress.ip_network(
                network_record["IPAM"]["Config"][0]["Subnet"], strict=False
            )
            if subnet.version != 4 or not subnet.is_private or subnet.num_addresses > 65536:
                raise RuntimeError("Unexpected acceptance Web subnet")
            used = {
                ipaddress.ip_address(value["IPv4Address"].split("/")[0])
                for value in (network_record.get("Containers") or {}).values()
                if value.get("IPv4Address")
            }
            gateway = network_record["IPAM"]["Config"][0].get("Gateway")
            if gateway:
                used.add(ipaddress.ip_address(gateway))
            # Retain addresses used by prior protocol runs as well; Redis
            # rate-limit windows outlive a stopped container.
            for prior in (ROOT / ".cache/track-acceptance").rglob("browser-web.json"):
                try:
                    previous_ip = json.loads(prior.read_text(encoding="utf-8")).get("container_ip")
                    if previous_ip:
                        used.add(ipaddress.ip_address(previous_ip))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            candidates = [address for address in subnet.hosts() if address not in used]
            if candidates:
                # Avoid reusing a protocol container's source address; product
                # rate-limit buckets intentionally persist after it stops.
                ip_args = ["--ip", str(random.choice(candidates))]
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            ip_args = []
    execute(
        [
            docker,
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--network",
            network,
            *ip_args,
            "--publish",
            "127.0.0.1::3000",
            "--env",
            "API_BASE_URL=http://api:8000",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            image,
        ],
        log,
    )
    try:
        port = subprocess.run(
            [docker, "port", name, "3000/tcp"], capture_output=True, check=False, timeout=15
        )
        address = port.stdout.decode().strip()
        if port.returncode or not address.startswith("127.0.0.1:") or not address[10:].isdigit():
            raise RuntimeError("Unexpected browser Web binding")
        origin = "http://" + address
        deadline = time.monotonic() + 45
        with httpx.Client(timeout=2, trust_env=False) as client:
            while True:
                try:
                    if client.get(origin + "/progress").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("Isolated browser Web did not become ready")
                time.sleep(0.5)
        (directory / "browser-web.json").write_text(
            json.dumps(
                {
                    "image": image,
                    "origin": origin,
                    "container_ip": ip_args[1] if ip_args else None,
                    "temporary": True,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        yield origin
    finally:
        # Only the UUID-named container created above; --rm cleans the container,
        # with no mounts, user data or volumes attached.
        execute([docker, "stop", "--time", "10", name], log)


def browser(track: str, directory: Path) -> None:
    from scripts.operations_check import load_acceptance, load_runtime_storage

    values = load_acceptance()
    storage = load_runtime_storage(values)
    environment = {
        **os.environ,
        **values,
        **storage,
        "APP_ENV": "test",
        "RUN_STACK_INTEGRATION": "1",
        "API_BASE_URL": "http://127.0.0.1:18000",
        "E2E_BASE_URL": "http://127.0.0.1:13000",
        "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "CREDENTIAL_KEYS": '{"acceptance":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        "CREDENTIAL_ACTIVE_KEY": "acceptance",
        "E2E_REPORT_FILE": str(directory / "browser.json"),
    }
    files = SUITES[track]["browser"]
    arguments = [argument for name in files for argument in ("--test-file", name)]
    with contextlib.ExitStack() as contexts:
        origin = contexts.enter_context(isolated_browser_web(directory))
        environment["E2E_BASE_URL"] = origin
        if "topic-hierarchy.spec.ts" in files:
            topic_directory = directory / "topic-web"
            topic_directory.mkdir()
            environment["E2E_HIERARCHY_BASE_URL"] = contexts.enter_context(
                isolated_browser_web(topic_directory)
            )
        execute(
            [sys.executable, str(ROOT / "scripts/account_e2e.py"), *arguments],
            directory / "browser.log",
            environment,
        )
    counts = verify_browser_report(directory / "browser.json")
    (directory / "browser-summary.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", choices=SUITES, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="Read-only plan; no services contacted")
    mode.add_argument("--run", choices=("static", "integration", "browser"))
    arguments = parser.parse_args()
    record = plan(arguments.track)
    if arguments.list:
        print(json.dumps(record, ensure_ascii=True, indent=2))
        return 0
    stage = arguments.run
    directory = ROOT / ".cache" / "track-acceptance" / uuid4().hex
    directory.mkdir(parents=True)
    record.update(stage=stage, started_at=datetime.now(UTC).isoformat(), status="running")
    try:
        if not suite_files(arguments.track, stage):
            record["status"] = "unverified"
        else:
            if stage == "static":
                execute(
                    ["powershell.exe", "-NoProfile", "-File", "scripts/check.ps1"],
                    directory / "static.log",
                )
            elif stage == "integration":
                integration(arguments.track, directory)
            else:
                browser(arguments.track, directory)
            record["status"] = "passed_implemented_scope"
    except Exception as error:
        record.update(status="failed", error=type(error).__name__)
    record["completed_at"] = datetime.now(UTC).isoformat()
    (directory / "summary.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"{record['status']}: {directory / 'summary.json'}")
    return 0 if record["status"] == "passed_implemented_scope" else 1


if __name__ == "__main__":
    # Support the documented direct-script entry and module imports in pytest.
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())

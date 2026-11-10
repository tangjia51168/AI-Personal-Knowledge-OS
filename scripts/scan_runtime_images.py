"""Scan the actual local acceptance images, bound to immutable image IDs.

Docker Scout CVE matching sends package identifiers and layer digests to Docker's
service. Both command-line opt-ins are required. No image is pushed or pulled,
and container environment/configuration values are never requested or reported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.operations_check import PROJECT, REQUIRED_SERVICES, ROOT, docker_path

REPORT_ROOT = ROOT / ".cache" / "scout-reports"
SERVICES = frozenset(
    (*REQUIRED_SERVICES, "gateway", "migrate", "storage-init", "acceptance", "embedding")
)
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
REPO_DIGEST = re.compile(r"[a-z0-9./:_-]+@sha256:[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:+-]{1,160}\Z")
MAX_SARIF_BYTES = 128 * 1024 * 1024
CONTAINER_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"image_id":{{json .Image}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"running":{{json .State.Running}}}'
)
IMAGE_FORMAT = '{"image_id":{{json .Id}},"repo_digests":{{json .RepoDigests}}}'


class ScanFailure(RuntimeError):
    """A fixed, public error code; no Docker stderr or configuration values."""


def allowed_container_name(name: str, service: str) -> bool:
    if re.fullmatch(r"/?knowledge-os-acceptance-[a-zA-Z0-9_.-]{1,150}", name):
        return True
    if service == "acceptance" and re.fullmatch(
        r"/?knowledge-os-(?:track-tests|semantic-test)-[0-9a-f]{12}", name
    ):
        return True
    return service in {"api", "worker", "beat", "minio"} and bool(
        re.fullmatch(r"/?kos-recovery-[0-9a-f]{32}-" + service, name)
    )


def now() -> str:
    return datetime.now(UTC).isoformat()


def run_docker(*args: str, timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(
            [docker_path(), *args], capture_output=True, check=False, timeout=timeout
        )
    except Exception:
        raise ScanFailure("DOCKER_COMMAND_FAILED") from None
    if result.returncode:
        raise ScanFailure("DOCKER_COMMAND_FAILED")
    return result.stdout


def read_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        raise ScanFailure("INVALID_JSON") from None


def inventory() -> list[dict[str, Any]]:
    """Read only selected safe fields from this one project's containers."""
    raw = run_docker(
        "ps", "-aq", "--no-trunc", "--filter", f"label=com.docker.compose.project={PROJECT}"
    )
    try:
        ids = raw.decode("ascii").split()
    except UnicodeError:
        raise ScanFailure("INVALID_CONTAINER_INVENTORY") from None
    if not ids or len(ids) > 128 or len(set(ids)) != len(ids):
        raise ScanFailure("EMPTY_OR_INVALID_CONTAINER_INVENTORY")
    if not all(CONTAINER_ID.fullmatch(item) for item in ids):
        raise ScanFailure("INVALID_CONTAINER_INVENTORY")
    containers = []
    for container_id in sorted(ids):
        item = read_json(run_docker("inspect", "--format", CONTAINER_FORMAT, container_id))
        if not isinstance(item, dict):
            raise ScanFailure("INVALID_CONTAINER_INVENTORY")
        name = item.get("name")
        image_id = item.get("image_id")
        if (
            item.get("id") != container_id
            or item.get("project") != PROJECT
            or item.get("service") not in SERVICES
            or not isinstance(name, str)
            or not allowed_container_name(name, item["service"])
            or not isinstance(image_id, str)
            or SHA256.fullmatch(image_id) is None
            or not isinstance(item.get("running"), bool)
        ):
            raise ScanFailure("INVALID_CONTAINER_INVENTORY")
        containers.append(
            {
                "container_id": container_id,
                "name": name.lstrip("/"),
                "service": item["service"],
                "image_id": image_id,
                "running": item["running"],
            }
        )
    running = {item["service"] for item in containers if item["running"]}
    if not set(REQUIRED_SERVICES).issubset(running):
        raise ScanFailure("REQUIRED_RUNTIME_SERVICES_MISSING")
    return containers


def image_metadata(image_id: str) -> dict[str, Any]:
    if SHA256.fullmatch(image_id) is None:
        raise ScanFailure("INVALID_IMAGE_ID")
    item = read_json(run_docker("image", "inspect", "--format", IMAGE_FORMAT, image_id))
    if not isinstance(item, dict) or item.get("image_id") != image_id:
        raise ScanFailure("IMAGE_ID_MISMATCH")
    digests = item.get("repo_digests") or []
    if not isinstance(digests, list) or any(
        not isinstance(digest, str) or REPO_DIGEST.fullmatch(digest) is None for digest in digests
    ):
        raise ScanFailure("INVALID_REPOSITORY_DIGEST")
    return {"image_id": image_id, "repo_digests": sorted(set(digests))}


def sarif_summary(path: Path) -> dict[str, Any]:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_SARIF_BYTES:
        raise ScanFailure("SARIF_MISSING_OR_TOO_LARGE")
    raw = path.read_bytes()
    data = read_json(raw)
    if not isinstance(data, dict) or data.get("version") != "2.1.0":
        raise ScanFailure("INVALID_SARIF")
    runs = data.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ScanFailure("INVALID_SARIF")
    levels: Counter[str] = Counter()
    versions = set()
    findings = []
    for run in runs:
        if not isinstance(run, dict):
            raise ScanFailure("INVALID_SARIF")
        tool = run.get("tool")
        driver = tool.get("driver") if isinstance(tool, dict) else None
        if not isinstance(driver, dict) or driver.get("name") != "docker scout":
            raise ScanFailure("INVALID_SARIF_SCANNER")
        version = driver.get("version")
        if not isinstance(version, str) or IDENTIFIER.fullmatch(version) is None:
            raise ScanFailure("INVALID_SARIF_SCANNER")
        versions.add(version)
        rules = driver.get("rules")
        results = run.get("results")
        if not isinstance(rules, list) or not isinstance(results, list):
            raise ScanFailure("INVALID_SARIF")
        by_id = {}
        for rule in rules:
            if (
                not isinstance(rule, dict)
                or not isinstance(rule.get("id"), str)
                or IDENTIFIER.fullmatch(rule["id"]) is None
                or rule["id"] in by_id
            ):
                raise ScanFailure("INVALID_SARIF_RULE")
            by_id[rule["id"]] = rule
        for result in results:
            if not isinstance(result, dict):
                raise ScanFailure("INVALID_SARIF_RESULT")
            rule_id = result.get("ruleId")
            level = result.get("level", "warning")
            if not isinstance(rule_id, str) or rule_id not in by_id:
                raise ScanFailure("INVALID_SARIF_RULE_REFERENCE")
            if not isinstance(level, str) or level not in {"error", "warning", "note", "none"}:
                raise ScanFailure("INVALID_SARIF_LEVEL")
            properties = by_id[rule_id].get("properties", {})
            if not isinstance(properties, dict):
                raise ScanFailure("INVALID_SARIF_PROPERTIES")
            purls = properties.get("purls")
            fixed = properties.get("fixed_version")
            if (
                not isinstance(purls, list)
                or not purls
                or any(
                    not isinstance(purl, str)
                    or not purl.startswith("pkg:")
                    or len(purl) > 4096
                    or any(ord(char) < 32 for char in purl)
                    for purl in purls
                )
                or (fixed is not None and (not isinstance(fixed, str) or len(fixed) > 512))
            ):
                raise ScanFailure("INVALID_SARIF_PACKAGE")
            levels[level] += 1
            findings.append({"id": rule_id, "level": level, "purls": purls, "fixed_version": fixed})
    failed = bool(levels["error"] or levels["warning"])
    return {
        "status": "failed_vulnerability_gate" if failed else "passed",
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "scanner_versions": sorted(versions),
        "results": sum(levels.values()),
        "levels": dict(sorted(levels.items())),
        "findings": findings,
    }


def write_summary(directory: Path, summary: dict[str, Any]) -> Path:
    path = directory / "summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def gate_status(records: list[dict[str, Any]], *, complete: bool) -> str:
    statuses = {record.get("status") for record in records}
    if not complete or not statuses or not statuses <= {"passed", "failed_vulnerability_gate"}:
        return "failed_scan"
    if "failed_vulnerability_gate" in statuses:
        return "failed_vulnerability_gate"
    return "passed"


def scan_acceptance() -> Path:
    directory = REPORT_ROOT / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {
        "status": "running",
        "started_at": now(),
        "project": PROJECT,
        "scope": "local immutable images of acceptance containers, including exited helpers",
        "authorized_external_metadata": "PURL and image layer digests only",
        "image_transfer": "no image push, pull, or upload",
        "containers": [],
        "images": [],
        "errors": [],
        "running_runtime_gate": "running",
        "all_inventoried_images_gate": "running",
    }
    write_summary(directory, summary)
    try:
        before = inventory()
        summary["containers"] = before
        write_summary(directory, summary)

        def scan_image(image_id: str) -> dict[str, Any]:
            record: dict[str, Any] = {
                "image_id": image_id,
                "services": sorted(
                    {item["service"] for item in before if item["image_id"] == image_id}
                ),
                "running_services": sorted(
                    {
                        item["service"]
                        for item in before
                        if item["image_id"] == image_id and item["running"]
                    }
                ),
                "exited_services": sorted(
                    {
                        item["service"]
                        for item in before
                        if item["image_id"] == image_id and not item["running"]
                    }
                ),
                "started_at": now(),
            }
            try:
                record.update(image_metadata(image_id))
                report = directory / (image_id.removeprefix("sha256:") + ".sarif.json")
                record["report"] = report.name
                run_docker(
                    "scout",
                    "cves",
                    "local://" + image_id,
                    "--format",
                    "sarif",
                    "--output",
                    str(report),
                    timeout=300,
                )
                record.update(sarif_summary(report))
            except Exception as exc:
                record.update(
                    status="failed_scan",
                    error=str(exc) if isinstance(exc, ScanFailure) else "SCAN_FAILED",
                )
            record["completed_at"] = now()
            return record

        running_ids = {item["image_id"] for item in before if item["running"]}
        image_ids = sorted(
            {item["image_id"] for item in before},
            key=lambda value: (value not in running_ids, value),
        )
        # Bound CPU/network demand. Only this main thread writes the report; workers
        # write their own Image-ID-named SARIF. All images and the strict gate remain.
        summary["max_parallel_scans"] = 3
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(scan_image, image_id) for image_id in image_ids]
            for future in as_completed(futures):
                summary["images"].append(future.result())
                summary["images"].sort(key=lambda record: record["image_id"])
                write_summary(directory, summary)
        if inventory() != before:
            raise ScanFailure("CONTAINER_INVENTORY_CHANGED")
        summary["inventory_stable"] = True
    except Exception as exc:
        summary["errors"].append(str(exc) if isinstance(exc, ScanFailure) else "SCAN_FAILED")
    complete = bool(summary.get("inventory_stable")) and not summary["errors"]
    all_gate = gate_status(summary["images"], complete=complete)
    running_images = [record for record in summary["images"] if record["running_services"]]
    running_services = {
        service for record in running_images for service in record["running_services"]
    }
    # A partial scan cannot certify either scope. Historical vulnerability findings
    # remain in the strict overall gate without mislabeling current runtime images.
    runtime_complete = (
        complete and all_gate != "failed_scan" and set(REQUIRED_SERVICES).issubset(running_services)
    )
    summary["running_runtime_gate"] = gate_status(running_images, complete=runtime_complete)
    summary["all_inventoried_images_gate"] = all_gate
    summary["status"] = all_gate
    summary["completed_at"] = now()
    summary["scanner_versions"] = sorted(
        {version for record in summary["images"] for version in record.get("scanner_versions", [])}
    )
    return write_summary(directory, summary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acceptance", action="store_true", help="Use only the fixed local acceptance project"
    )
    parser.add_argument(
        "--allow-metadata-egress",
        action="store_true",
        help="Confirm prior authorization to send PURL and layer digests to Docker Scout",
    )
    args = parser.parse_args()
    if not args.acceptance or not args.allow_metadata_egress:
        parser.error("Both --acceptance and --allow-metadata-egress are required")
    try:
        path = scan_acceptance()
        status = json.loads(path.read_text(encoding="utf-8"))["status"]
    except Exception:
        print("FAIL: scan could not produce a report")
        return 1
    print(f"{status}: {path}")
    return {"passed": 0, "failed_vulnerability_gate": 2}.get(status, 1)


if __name__ == "__main__":
    raise SystemExit(main())

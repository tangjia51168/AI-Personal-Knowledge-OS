"""Explicit real CPU + isolated pgvector acceptance, preserving failure reports."""

import argparse
import contextlib
import json
import os
import sys
from uuid import uuid4
from xml.etree import ElementTree as ET

from scripts.operations_check import (
    ROOT,
    compose_args,
    docker_path,
    load_acceptance,
    load_runtime_storage,
)
from scripts.track_acceptance import execute, isolated_browser_web, verify_browser_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    values = load_acceptance()
    compose = compose_args(values)
    compose[3:3] = ["--env-file", str(ROOT / ".env.storage-runtime.acceptance")]
    for name in ("storage-runtime", "hardened", "embedding", "embedding-acceptance"):
        compose += ["-f", str(ROOT / f"docker-compose.{name}.yml")]
    directory = ROOT / ".cache/semantic-acceptance" / uuid4().hex
    directory.mkdir(parents=True)
    name = "knowledge-os-semantic-test-" + uuid4().hex[:12]
    docker = docker_path()
    report = {"status": "running", "scope": "local_cpu_pgvector_only"}
    try:
        if args.browser:
            environment = {
                **os.environ,
                **values,
                **load_runtime_storage(values),
                "APP_ENV": "test",
                "RUN_STACK_INTEGRATION": "1",
                "API_BASE_URL": "http://127.0.0.1:18000",
                "E2E_REPORT_FILE": str(directory / "browser.json"),
                "CREDENTIAL_KEYS": '{"acceptance":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
                "CREDENTIAL_ACTIVE_KEY": "acceptance",
            }
            with contextlib.ExitStack() as contexts:
                environment["E2E_BASE_URL"] = contexts.enter_context(
                    isolated_browser_web(directory)
                )
                execute(
                    [
                        sys.executable,
                        "-m",
                        "scripts.account_e2e",
                        "--test-file",
                        "semantic-search.spec.ts",
                    ],
                    directory / "browser.log",
                    environment,
                )
            report.update(
                status="passed", browser=verify_browser_report(directory / "browser.json")
            )
            print(f"passed: {directory / 'summary.json'}")
            return
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
                "--junitxml=/tmp/semantic.xml",
                "tests/test_semantic_search_integration.py",
            ],
            directory / "integration.log",
        )
        execute(
            [docker, "cp", name + ":/tmp/semantic.xml", str(directory / "junit.xml")],
            directory / "integration.log",
        )
        root = ET.parse(directory / "junit.xml").getroot()
        suites = list(root.iter("testsuite"))
        counts = {
            key: sum(int(s.get(key, "0")) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
        if not counts["tests"] or any(counts[k] for k in ("failures", "errors", "skipped")):
            raise ValueError("SEMANTIC_ACCEPTANCE_INCOMPLETE")
        report.update(status="passed", **counts)
    except Exception:
        report["status"] = "failed"
    finally:
        (directory / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{report['status']}: {directory / 'summary.json'}")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

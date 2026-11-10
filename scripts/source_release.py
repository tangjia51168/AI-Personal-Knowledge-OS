"""Prepare and verify an independent public source snapshot; never uploads or reads dotenv."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TOP = (
    ".dockerignore",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    "LICENSE",
    "alembic.ini",
    "pyproject.toml",
    "requirements.lock",
    "package.json",
    "package-lock.json",
    "ARCHITECTURE.md",
    "DATABASE_DESIGN.md",
)
TREES = (
    "apps/api/app",
    "apps/api/tests",
    "apps/api/migrations",
    "apps/worker/worker",
    "apps/worker/tests",
    "apps/web/src",
    "apps/web/tests",
    "apps/browser-extension",
    "apps/local-agent",
    "infrastructure",
    "requirements",
    "scripts",
    "tests",
    "packages",
)
WEB = (
    "package.json",
    "next.config.ts",
    "tsconfig.json",
    "next-env.d.ts",
    "postcss.config.mjs",
    "eslint.config.mjs",
    "playwright.config.ts",
)
DOCS = (
    "account-recovery",
    "account-security-history",
    "account-session",
    "audit-archive",
    "authorized-content-import",
    "backup-recovery",
    "collections",
    "concept-cards",
    "content-knowledge-pipeline",
    "deployable-release",
    "deployment-preparation",
    "development",
    "evidence-recall",
    "foundation-infrastructure",
    "knowledge-organization-track",
    "label-management",
    "learning-analysis",
    "learning-review",
    "learning-schedule",
    "learning-status",
    "learning-summary",
    "long-analysis",
    "maintenance",
    "manual-note-graph",
    "manual-note-labels",
    "manual-note-links",
    "module-delivery",
    "note-annotations",
    "note-editing",
    "note-export",
    "notifications",
    "obsidian-export",
    "open-source-release",
    "operations",
    "phase1-source-boundary",
    "project-status",
    "reference-workspace",
    "release-gates",
    "reminder-batch",
    "research-answers",
    "review-plan",
    "review-workbench",
    "runtime-image-security",
    "runtime-vulnerability-followup",
    "semantic-search",
    "source-note-filtering",
    "source-schedules",
    "spec-alignment",
    "storage-runtime-policy",
    "structured-note-reader",
    "tls-gateway",
    "topic-hierarchy",
    "v1-product",
)
SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".svg",
    ".json",
    ".mjs",
    ".cjs",
    ".sh",
    ".ps1",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".md",
    ".lock",
    ".template",
    ".conf",
    ".mako",
}
EXCLUDED = {
    "node_modules",
    "__pycache__",
    ".next",
    ".cache",
    ".git",
    ".venv",
    "test-results",
    "playwright-report",
    "build",
    "dist",
}
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024

README = """# AI Personal Knowledge OS

可自托管的证据型个人知识管理系统，使用 Next.js/TypeScript、FastAPI/SQLAlchemy、
PostgreSQL/pgvector、Celery/Redis 与私有 S3。当前是持续开发中的预览版本。

支持来源批次与授权文本/字幕导入、网页快照、分段证据笔记、人工版本/批注、主题与概念、
共享收藏/合集、本地语义检索、基础引用问答、学习提醒和多格式导出。
单次登录会话12小时，全部组件共用，权限由后端成员/RLS复验。

本仓库公开源码，不提供托管在线服务。真实平台访问、模型语义质量、外部邮箱、
真正异地恢复、客户端兼容性和生产安全有未验收部分；当前镜像漏洞门禁未通过。
不要将本机/协议测试通过理解为生产发布通过。完整差距见 [规格映射](docs/spec-alignment.md)。

## 本机启动

需要自行准备 Python 3.12、Node.js >=22、Docker Engine/Desktop 与 Compose >=2.24.4。
现有依赖锁覆盖 Windows amd64 / Linux glibc amd64；不自动安装系统工具。
以下命令在 Windows PowerShell 项目根目录执行：

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install --require-hashes --only-binary=:all: `
  -r requirements/build.lock
./.venv/Scripts/python.exe -m pip install --require-hashes --only-binary=:all: `
  -r requirements/dev.lock
./.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
npm ci
Copy-Item .env.example .env
# 编辑.env，替换所有CHANGE_ME；数据库URL密码必须与相应角色一致。
./.venv/Scripts/python.exe scripts/prepare_minio.py
docker compose up --build -d
```

开发入口为 http://localhost:3000/account ，项目成果为 /progress，API为 localhost:8000/docs。
所有端口仅限本机。持久化使用命名卷，备份与恢复流程见 docs/backup-recovery.md。
不要执行 down -v；已有数据库改密码需显式轮换，不是只改.env。
模型调用与公开采集默认关闭；须配置自己的凭据并明确开启，模型/二进制/秘密不随源码分发。

## 检查与维护

```powershell
./scripts/check.ps1
./scripts/acceptance.ps1 -Hardened -RestartCheck -Embedding
```

第二条只操作独立验收项目/测试库，不在生产库运行。外部服务缺失记未验收。
文档中的历史本机报告路径用于说明验收范围，报告和用户数据不包含在此仓库。
生产部署按 docs/deployable-release.md 分离运行与迁移秘密，并先完成 docs/release-gates.md。
维护边界见 docs/maintenance.md，数据库迁移冻结，业务模块与外部集成分层。

## 许可证

原创项目源码按 Apache-2.0，见 LICENSE 和 NOTICE。第三方依赖、模型及独立运行服务
保留其各自许可证；MinIO 为独立 AGPL-3.0 服务，未包含二进制或授予其 Apache 许可。
请查阅 THIRD_PARTY.md。此公开快照排除了私人需求附件、会话和所有运行数据。
"""


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and "\\" not in name
        and ":" not in name
        and all(part not in {".", "..", *EXCLUDED} for part in path.parts)
        and path.as_posix() == name
    )


def inspect_content(name: str, data: bytes) -> None:
    """Fail closed for common credential/private artifacts; known test literals are bounded."""
    if (
        not safe_name(name)
        or len(data) > MAX_FILE
        or b"\x00" in data
        or (PurePosixPath(name).name.startswith(".env") and name != ".env.example")
    ):
        raise ValueError("UNSAFE_SOURCE_FILE")
    content = data.decode("utf-8")
    patterns = (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}",
        r"\bgithub_pat_[A-Za-z0-9_]{30,}",
        r"\bsk-[A-Za-z0-9_-]{24,}",
        r"\bAKIA[A-Z0-9]{16}\b",
        r"https://chatgpt[.]com/(?:share/|s/t_)",
        r"wxid_[a-z0-9_]{8,}",
        r"[A-Za-z]:[/\\](?:Users[/\\][^/\\\s]+|xwechat_files)",
    )
    if any(re.search(pattern, content) for pattern in patterns):
        raise ValueError("SOURCE_PRIVATE_OR_SECRET_CONTENT")
    for match in re.finditer(r"[a-z][a-z0-9+.-]*://[^\s/:'\"]+:([^\s/@'\"]+)@", content):
        password = match[1]
        # These are intentionally invalid test/placeholder literals, never arbitrary values.
        if "${" in password or "CHANGE_ME" in password or "{" in password:
            continue
        if password in {
            "secret",
            "password",
            "private",
            "private-password",
            "private-key",
            "local-fixture",
            "unused",
            "unconfigured",
            "%00",
            "private%40password",
            "do-not-print",
            "private-secret",
            "never-print-this-password",
            "never-print-db-password",
            "private-redis-secret",
            "p",
            "b",
        }:
            continue
        raise ValueError("SOURCE_LITERAL_CREDENTIAL")


def public_payload(root: Path) -> dict[str, bytes]:
    names = {*TOP, *(f"apps/web/{name}" for name in WEB), *(f"docs/{name}.md" for name in DOCS)}
    names.update(path.name for path in root.glob("docker-compose*.yml"))
    for tree in TREES:
        for path in (root / tree).rglob("*"):
            relative = path.relative_to(root)
            if any(part in EXCLUDED or part.endswith(".egg-info") for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError("SOURCE_SYMLINK")
            if (
                path.is_file()
                and (
                    path.suffix in SUFFIXES
                    or path.name.endswith("Dockerfile")
                    or path.name.endswith(".env.example")
                )
                and not path.name.endswith(".tsbuildinfo")
            ):
                names.add(relative.as_posix())
    payload: dict[str, bytes] = {}
    for name in sorted(names):
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError("SOURCE_PATH_ESCAPE_OR_MISSING")
        payload[name] = path.read_bytes()
    # Replace the one local example path, never silently redact credential findings.
    foundation = "docs/foundation-infrastructure.md"
    payload[foundation] = payload[foundation].replace(
        b"E:\\\\Ai_Personal_Knowledge_OS\\\\.cache\\\\recovery-target.json",
        b"<workspace>/.cache/recovery-target.json",
    )
    payload.update(
        {
            "README.md": README.encode(),
            "PROGRESS.md": (
                b"# Public snapshot status\n\nSee README.md and docs/spec-alignment.md.\n"
            ),
            "TASKS.md": (
                b"# Roadmap\n\nSee docs/spec-alignment.md; all 12 full tracks remain partial.\n"
            ),
            "PROJECT_SPEC.md": (root / "docs/spec-alignment.md").read_bytes(),
            "docs/mainline-acceptance.md": (root / "docs/spec-alignment.md").read_bytes(),
            "storage/.gitkeep": b"",
            "NOTICE": (
                b"AI Personal Knowledge OS\n"
                b"Copyright 2026 AI Personal Knowledge OS contributors\n"
                b"Original source under Apache-2.0; external dependencies retain their licenses.\n"
            ),
            "THIRD_PARTY.md": (
                "# Third-party boundaries\n\nExact Python/npm versions, hashes and licenses "
                "metadata are inventoried by `python scripts/foundation_audit.py --inventory`. "
                "This is an inventory, not legal approval. See lockfiles and upstream licenses.\n\n"
                "MinIO is an independent AGPL-3.0 service downloaded by explicit scripts. "
                "No MinIO binaries or model weights are included. Redis, PostgreSQL/pgvector, "
                "Node, Python and images have their own terms. The multilingual MiniLM model is "
                "referenced at a pinned revision; verify its model card before redistribution.\n"
            ).encode("ascii"),
            "SECURITY.md": (
                "# Security\n\nThis is a development preview. Known runtime image vulnerability "
                "gates are open; see docs/runtime-vulnerability-followup.md. "
                "Do not expose development Compose or acceptance credentials publicly. "
                "No hosted SaaS deployment is verified. "
                "Never include secrets or private content in public issues. Use GitHub private "
                "vulnerability reporting when the repository owner enables it.\n"
            ).encode("ascii"),
        }
    )
    payload = {name: data.replace(b"\r\n", b"\n") for name, data in payload.items()}
    for name, data in payload.items():
        try:
            inspect_content(name, data)
        except (ValueError, UnicodeError):
            raise ValueError(f"SOURCE_REVIEW_REQUIRED: {name}") from None
    if sum(map(len, payload.values())) > MAX_TOTAL:
        raise ValueError("SOURCE_SIZE_LIMIT")
    return payload


def prepare(root: Path = ROOT) -> Path:
    payload = public_payload(root)
    manifest = {
        "format": "knowledge-os-public-source-v1",
        "schema": "0031_collections",
        "scope": "source_preview",
        "hosted_service_verified": False,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()},
    }
    directory = root / ".cache/source-releases" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    staging = directory / "source"
    staging.mkdir()
    for name, data in payload.items():
        destination = staging / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (staging / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    archive = directory / "knowledge-os-source.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(staging).as_posix())
    verify(staging)
    return staging


def verify(staging: Path) -> None:
    if staging.is_symlink():
        raise ValueError("SOURCE_SYMLINK")
    manifest = json.loads((staging / "SOURCE_MANIFEST.json").read_text("utf-8"))
    if (
        manifest.get("format") != "knowledge-os-public-source-v1"
        or manifest.get("hosted_service_verified") is not False
    ):
        raise ValueError("SOURCE_MANIFEST_INVALID")
    expected = manifest["files"]
    actual = set()
    for path in staging.rglob("*"):
        name = path.relative_to(staging).as_posix()
        if path.is_symlink():
            raise ValueError("SOURCE_SYMLINK")
        if ".git" in path.relative_to(staging).parts:
            continue
        if path.is_file() and name != "SOURCE_MANIFEST.json":
            actual.add(name)
            data = path.read_bytes()
            inspect_content(name, data)
            if hashlib.sha256(data).hexdigest() != expected.get(name):
                raise ValueError("SOURCE_CHECKSUM_MISMATCH")
    if (
        actual != set(expected)
        or sum((staging / name).stat().st_size for name in actual) > MAX_TOTAL
    ):
        raise ValueError("SOURCE_MANIFEST_MISMATCH")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            verify(args.verify)
            print("PASS: public source checksums and bounded content scan")
        else:
            print(f"PASS: public source candidate: {prepare()}")
    except Exception as error:
        # Only reviewed relative filenames may be disclosed; never raw credential content.
        print(
            str(error)
            if str(error).startswith("SOURCE_REVIEW_REQUIRED: ")
            else type(error).__name__
        )
        raise SystemExit("FAIL: public source preparation; no upload occurred") from None


if __name__ == "__main__":
    main()

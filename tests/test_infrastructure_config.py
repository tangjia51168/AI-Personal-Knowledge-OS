from pathlib import Path

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_compose_structure_without_docker():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    assert set(compose["services"]) == {
        "postgres",
        "redis",
        "minio",
        "storage-init",
        "migrate",
        "api",
        "worker",
        "beat",
        "web",
    }
    for service in compose["services"].values():
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:")
    for service in ("api", "worker", "beat"):
        assert "MIGRATION_DATABASE_URL" not in compose["services"][service]["environment"]
    assert "postgres_data" in compose["volumes"]
    minio = compose["services"]["minio"]
    assert minio["pull_policy"] == "build"
    assert minio["build"]["context"] == "./.cache/minio"
    assert minio["image"].endswith("-local")


def test_migration_graph():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_heads() == ["0031_collections"]
    latest = script.get_heads()[0]
    for consumer in (
        "apps/api/app/modules/system/health.py",
        "scripts/operations_check.py",
    ):
        assert f'if revision != "{latest}"' in Path(consumer).read_text(encoding="utf-8")


def test_python_build_and_linux_runtime_dependencies_are_pinned():
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    lock = Path("requirements.lock").read_text(encoding="utf-8")
    assert 'requires = ["setuptools==84.2.1"]' in project
    assert 'uvloop==0.23.0; sys_platform != "win32"' in lock


def test_acceptance_override_is_isolated():
    class ComposeLoader(yaml.SafeLoader):
        pass

    ComposeLoader.add_constructor("!override", lambda loader, node: loader.construct_sequence(node))
    override = yaml.load(
        Path("docker-compose.acceptance.yml").read_text(encoding="utf-8"), Loader=ComposeLoader
    )
    services = override["services"]
    assert override["volumes"]["redis_data"]["name"] == (
        "${ACCEPTANCE_REDIS_VOLUME:-knowledge-os-acceptance_redis_data}"
    )
    assert services["postgres"]["ports"] == ["127.0.0.1:15432:5432"]
    assert services["web"]["ports"] == ["127.0.0.1:13000:3000"]
    assert services["acceptance"]["build"]["target"] == "test"
    assert services["acceptance"]["environment"]["RUN_STACK_INTEGRATION"] == "1"

"""Collection boundary and irreversible migration contracts; no fabricated service success."""

from pathlib import Path

import pytest
from app.modules.organization.collections import CollectionInput, CollectionUpdate, ItemInput
from pydantic import ValidationError


@pytest.mark.parametrize("name", ["", "a\nb", "a\u202eb", "x" * 61])
def test_invalid_collection_names(name):
    with pytest.raises(ValidationError):
        CollectionInput(kind="folder", name=name)


def test_normalized_name_and_closed_fields():
    assert CollectionInput(kind="collection", name="  ＡＢＣ  收藏  ").name == "ABC 收藏"
    with pytest.raises(ValidationError):
        CollectionInput(kind="playlist", name="x")
    with pytest.raises(ValidationError):
        CollectionInput.model_validate({"kind": "folder", "name": "x", "tenant_id": "x"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_version", True),
        ("expected_version", 0),
        ("position", 200),
        ("position", -1),
        ("active", "true"),
    ],
)
def test_item_limits_are_strict(field, value):
    body = {"expected_version": 1, "active": True, "position": 0, field: value}
    with pytest.raises(ValidationError):
        ItemInput.model_validate(body)


def test_update_requires_current_version_and_explicit_state():
    with pytest.raises(ValidationError):
        CollectionUpdate.model_validate({"expected_version": 1, "name": "x"})


def test_fixed_collection_migration_does_not_import_mutable_metadata():
    content = Path("apps/api/migrations/versions/0031_collections.py").read_text("utf-8")
    assert "metadata" not in content and "app.modules" not in content
    assert 'down_revision = "0030_notifications"' in content
    assert "FORCE ROW LEVEL SECURITY" in content and "GRANT UPDATE(" in content

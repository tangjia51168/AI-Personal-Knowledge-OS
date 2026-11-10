"""Classification input boundaries and authentication before business reads/writes."""

from uuid import uuid4

import pytest
from app.modules.notes.listing import NoteListQuery
from app.modules.organization.content import LabelInput, LabelUpdateInput
from app.modules.organization.routes import router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("left", "right", "name"),
    [
        ("  电力\u00a0\u3000电子  ", "电力 电子", "电力 电子"),
        (" Ｓｉｍｕｌｉｎｋ ", "simulink", "Simulink"),
        ("Straße", "STRASSE", "Straße"),
        ("Cafe\u0301", "CAFÉ", "Café"),
    ],
)
def test_label_names_deduplicate_compatibility_case_and_space(left, right, name):
    body = LabelInput(kind="topic", name=left)
    assert body.name == name
    assert body.name_key == LabelInput(kind="topic", name=right).name_key
    assert LabelInput(kind="tag", name=left).name_key == body.name_key


@pytest.mark.parametrize(
    "name", ["", "  ", "x" * 61, "\u200b", "a\nb", "a\tb", "\u202eb", "\u2028", "\u2029", "\ud800"]
)
def test_label_rejects_blank_long_controls_bidi_and_unpaired_surrogate(name):
    with pytest.raises(ValidationError):
        LabelInput(kind="tag", name=name)


def test_label_accepts_exact_utf8_bound_and_closed_request_fields():
    body = LabelInput(kind="tag", name="😀" * 60)
    assert len(body.name.encode("utf-8")) == 240
    with pytest.raises(ValidationError):
        LabelInput(kind="topic", name="x", created_by=str(uuid4()))
    with pytest.raises(ValidationError):
        LabelInput(kind="concept", name="x")
    with pytest.raises(ValidationError):
        NoteListQuery(label_id="invalid")
    identifier = uuid4()
    assert NoteListQuery(label_id=identifier).label_id == identifier


def test_label_update_requires_version_and_bounded_changes():
    parent = uuid4()
    assert LabelUpdateInput(parent_id=parent, expected_version=1).parent_id == parent
    assert LabelUpdateInput(parent_id=None, expected_version=1).model_fields_set == {
        "parent_id",
        "expected_version",
    }
    with pytest.raises(ValidationError):
        LabelUpdateInput(parent_id="invalid", expected_version=1)
    assert LabelUpdateInput(name=" 新名称 ", expected_version=2).name == "新名称"
    assert LabelUpdateInput(active=False, expected_version=1).active is False
    with pytest.raises(ValidationError):
        LabelUpdateInput(expected_version=0)
    with pytest.raises(ValidationError):
        LabelUpdateInput(name="a\nb", expected_version=1)
    for payload in (
        {"expected_version": 1},
        {"expected_version": True, "active": False},
        {"expected_version": 1, "active": "false"},
        {"expected_version": 1, "active": None},
        {"expected_version": 1, "name": None},
        {"expected_version": 1, "kind": "tag", "active": True},
    ):
        with pytest.raises(ValidationError):
            LabelUpdateInput.model_validate(payload)


def test_organization_endpoints_require_auth_before_any_database_access():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    base = f"/api/v1/spaces/{uuid4()}"
    note, label = uuid4(), uuid4()
    for method, path, kwargs in (
        ("get", "/labels", {}),
        ("post", "/labels", {"json": {"kind": "topic", "name": "电力电子"}}),
        ("get", f"/notes/{note}/labels", {}),
        ("put", f"/notes/{note}/labels/{label}", {}),
        ("delete", f"/notes/{note}/labels/{label}", {}),
        ("get", "/labels/manage", {}),
        ("patch", f"/labels/{label}", {"json": {"active": False, "expected_version": 1}}),
    ):
        assert getattr(client, method)(base + path, **kwargs).status_code == 401

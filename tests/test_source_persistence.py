from uuid import uuid4

import pytest
from app.db.models import Base
from app.modules.sources.models import Source, SourceBatch
from app.modules.sources.routes import SourceImportInput, SourceInspectInput
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, UniqueConstraint


def test_source_tables_are_tenant_scoped_and_composite_related():
    assert SourceBatch.__table__.name == "source_batches"
    assert Source.__table__.name == "sources"
    assert {"tenant_id", "created_by"} in [
        {column.name for column in constraint.columns}
        for constraint in SourceBatch.__table__.foreign_key_constraints
    ]
    assert {"tenant_id", "batch_id"} in [
        {column.name for column in constraint.columns}
        for constraint in Source.__table__.foreign_key_constraints
    ]
    assert any(
        {column.name for column in constraint.columns} == {"tenant_id", "canonical_sha256"}
        for constraint in Source.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    )


def test_source_result_constraints_keep_accepted_and_rejected_shapes_closed():
    checks = [
        constraint.sqltext.text
        for constraint in Source.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    assert any("status IN" in check for check in checks)
    assert any(
        "canonical_url IS NOT NULL" in check and "rejection_code IS NULL" in check
        for check in checks
    )
    assert "sources" in Base.metadata.tables


@pytest.mark.parametrize("schema", [SourceImportInput, SourceInspectInput])
def test_source_text_limit_is_utf8_bytes_not_character_count(schema):
    payload = {"text": "汉" * 5461 + "a", "idempotency_key": uuid4()}
    assert len(schema(**payload).text.encode()) == 16384
    with pytest.raises(ValidationError):
        schema(**{**payload, "text": payload["text"] + "a"})

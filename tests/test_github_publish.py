"""Publication must stop before mutation for an unexpected account or an existing repository."""

from unittest.mock import Mock

import pytest

from scripts import github_publish as publication


@pytest.mark.parametrize(
    "identity", [{"login": "other", "id": 1}, {"login": publication.ACCOUNT, "id": 1}]
)
def test_account_mismatch_never_creates_git_or_repository(monkeypatch, tmp_path, identity):
    monkeypatch.setattr(publication, "verify", lambda _: None)
    monkeypatch.setattr(publication, "credential", lambda _: "test-token")
    monkeypatch.setattr(publication, "api", lambda *args, **kwargs: (200, identity))
    command = Mock()
    monkeypatch.setattr(publication, "execute", command)
    with pytest.raises(RuntimeError, match="ACCOUNT_MISMATCH"):
        publication.publish(tmp_path)
    command.assert_not_called()


def test_existing_repository_is_never_overwritten(monkeypatch, tmp_path):
    monkeypatch.setattr(publication, "verify", lambda _: None)
    monkeypatch.setattr(publication, "credential", lambda _: "test-token")
    responses = iter(
        [(200, {"login": publication.ACCOUNT, "id": publication.ACCOUNT_ID}), (200, {})]
    )
    monkeypatch.setattr(publication, "api", lambda *args, **kwargs: next(responses))
    command = Mock()
    monkeypatch.setattr(publication, "execute", command)
    with pytest.raises(RuntimeError, match="EXISTING_REPOSITORY"):
        publication.publish(tmp_path)
    command.assert_not_called()


def test_credential_is_not_written_to_disk_or_passed_in_arguments(monkeypatch, tmp_path):
    def command(arguments, cwd, *, input):
        assert arguments == ["git", "credential", "fill"]
        assert input == "protocol=https\nhost=github.com\n\n"
        return "username=user\npassword=" + "x" * 32

    monkeypatch.setattr(publication, "execute", command)
    assert publication.credential(tmp_path) == "x" * 32
    assert list(tmp_path.iterdir()) == []

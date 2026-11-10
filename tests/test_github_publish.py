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


def test_git_trust_is_scoped_to_verified_directory_and_decodes_utf8(monkeypatch, tmp_path):
    def run(arguments, **kwargs):
        assert arguments[:3] == ["git", "-c", f"safe.directory={tmp_path.resolve().as_posix()}"]
        assert "--global" not in arguments
        assert kwargs["encoding"] == "utf-8"
        return Mock(returncode=0, stdout="公开源码\n")

    monkeypatch.setattr(publication.subprocess, "run", run)
    assert publication.execute(["git", "status"], tmp_path) == "公开源码"


def test_github_api_uses_utf8_and_credentials_only_in_stdin(monkeypatch):
    token = "x" * 32

    def run(arguments, **kwargs):
        assert token not in " ".join(arguments)
        assert token in kwargs["input"]
        assert kwargs["encoding"] == "utf-8"
        assert "--insecure" not in arguments
        return Mock(returncode=0, stdout='{"description":"知识库"}\n201')

    monkeypatch.setattr(publication.subprocess, "run", run)
    assert publication.api(token, "/user/repos", payload={"name": "source"}) == (
        201,
        {"description": "知识库"},
    )

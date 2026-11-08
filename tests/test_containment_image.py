"""Image container bisa dipin ke digest lewat WTH_LAB_IMAGE."""

import importlib

import pytest

import wth_ai.containment as containment

_VALID_DIGEST = "deadbeef" * 8  # 64 hex


def test_explicit_image_is_used():
    argv = containment.SandboxSpec(image=f"python@sha256:{_VALID_DIGEST}").docker_args(
        ["sh", "-c", "true"])
    assert f"python@sha256:{_VALID_DIGEST}" in argv


def test_env_override_sets_default(monkeypatch):
    monkeypatch.setenv("WTH_LAB_IMAGE", f"python@sha256:{_VALID_DIGEST}")
    try:
        module = importlib.reload(containment)
        assert module.DEFAULT_IMAGE == f"python@sha256:{_VALID_DIGEST}"
        assert f"python@sha256:{_VALID_DIGEST}" in module.SandboxSpec().docker_args(["true"])
    finally:
        monkeypatch.delenv("WTH_LAB_IMAGE", raising=False)
        importlib.reload(containment)


def test_default_has_no_new_privileges_and_read_only():
    argv = containment.SandboxSpec().docker_args(["true"])
    joined = " ".join(argv)
    assert "--cap-drop ALL" in joined
    assert "no-new-privileges" in joined
    assert "--read-only" in argv
    assert "--network" in argv


@pytest.mark.parametrize("bad", [
    "python@sha256:short",
    "python@sha256:gggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggg",
    "@sha256:" + "deadbeef" * 8,  # tanpa nama image
])
def test_invalid_digest_rejected(bad):
    with pytest.raises(ValueError, match="digest tidak valid"):
        containment.SandboxSpec(image=bad).docker_args(["true"])


class _FakeRun:
    """Fake `subprocess.run` untuk `resolve_image_digest`."""

    def __init__(self, *, rc: int = 0, stdout: str = "", stderr: str = ""):
        self._rc = rc
        self._stdout = stdout
        self._stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, capture_output=False, text=False, timeout=None):
        self.calls.append(list(argv))
        return type("R", (), {
            "returncode": self._rc,
            "stdout": self._stdout,
            "stderr": self._stderr,
        })()


def test_resolve_tag_to_digest_via_docker():
    """B5: tag -> digest lewat inspect daemon, lalu dipin."""
    digest = _VALID_DIGEST
    fake = _FakeRun(stdout=f"python@sha256:{digest}")
    ok, resolved = containment.resolve_image_digest("python:3.12", run=fake)
    assert ok
    assert resolved == f"python@sha256:{digest}"
    # inspect dipanggil sekali dan dengan format RepoDigests
    assert fake.calls
    assert fake.calls[0][0] == "docker"
    assert fake.calls[0][1] == "image"


def test_resolve_fails_when_docker_missing(monkeypatch):
    monkeypatch.setattr(containment, "docker_available", lambda: False)
    ok, reason = containment.resolve_image_digest("python:3.12", run=_FakeRun())
    assert not ok
    assert "docker" in reason.lower()


def test_resolve_fails_on_nonzero_probe():
    fake = _FakeRun(rc=1, stderr="No such image")
    ok, reason = containment.resolve_image_digest("python:nonexistent", run=fake)
    assert not ok
    assert "gagal" in reason


def test_resolve_rejects_image_without_digest():
    fake = _FakeRun(stdout="python:3.12")  # RepoDigests kosong
    ok, reason = containment.resolve_image_digest("python:3.12", run=fake)
    assert not ok
    assert "digest" in reason.lower()


def test_resolve_passthrough_when_already_pinned():
    pinned = f"python@sha256:{_VALID_DIGEST}"
    fake = _FakeRun()
    ok, resolved = containment.resolve_image_digest(pinned, run=fake)
    assert ok
    assert resolved == pinned
    assert not fake.calls  # tidak menanyai daemon kalau sudah dipin

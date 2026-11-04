"""Image container bisa dipin ke digest lewat WTH_LAB_IMAGE."""

import importlib

import wth_ai.containment as containment


def test_explicit_image_is_used():
    argv = containment.SandboxSpec(image="python@sha256:deadbeef").docker_args(
        ["sh", "-c", "true"])
    assert "python@sha256:deadbeef" in argv


def test_env_override_sets_default(monkeypatch):
    monkeypatch.setenv("WTH_LAB_IMAGE", "python@sha256:cafe")
    try:
        module = importlib.reload(containment)
        assert module.DEFAULT_IMAGE == "python@sha256:cafe"
        assert "python@sha256:cafe" in module.SandboxSpec().docker_args(["true"])
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

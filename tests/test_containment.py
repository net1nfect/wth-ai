"""Containment: flag sandbox lengkap, network internal, tanpa published port."""

from wth_ai.containment import SandboxSpec, network_create_args


def test_sandbox_flags_are_complete():
    args = SandboxSpec().docker_args(["python", "-V"])
    joined = " ".join(args)
    for flag in ("--cap-drop ALL", "--security-opt no-new-privileges",
                 "--pids-limit 128", "--memory 512m", "--cpus 1.0", "--read-only"):
        assert flag in joined, flag
    assert "--network wth-lab-internal" in joined
    assert "-p" not in args and "--publish" not in args
    assert args[-2:] == ["python", "-V"]


def test_network_is_internal():
    assert network_create_args() == ["docker", "network", "create", "--internal",
                                     "wth-lab-internal"]
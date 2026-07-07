"""Docker packaging smoke tests (no docker required)."""

from pathlib import Path


def test_docker_files_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "Dockerfile").exists()
    assert (root / "docker-compose.yaml").exists()
    assert (root / "docker" / "entrypoint.sh").exists()
    assert (root / ".env.docker.example").exists()
    assert (root / "docs" / "DOCKER.md").exists()

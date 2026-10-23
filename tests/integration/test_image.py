from __future__ import annotations

import subprocess
from collections.abc import Iterator

import pytest

from .support import ROOT

pytestmark = pytest.mark.integration

IMAGE = "cce-api:test"
DOCKERFILE = "infrastructure/docker/api.Dockerfile"
DEV_MODULES = ["pytest", "pytest_asyncio", "pytest_cov", "mypy", "pip_audit", "asgi_lifespan"]


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False, cwd=ROOT)


def run_in_image(*command: str, user: str | None = None) -> subprocess.CompletedProcess[str]:
    """Executa na imagem, sem rede. `user="0"` serve só para inspecionar o sistema de arquivos."""
    options = ["--user", user] if user else []
    return docker("run", "--rm", "--network", "none", *options, IMAGE, *command)


@pytest.fixture(scope="module")
def image() -> Iterator[str]:
    build = docker("build", "--quiet", "-f", DOCKERFILE, "-t", IMAGE, ".")
    if build.returncode != 0:
        pytest.fail(f"image build failed:\n{build.stderr[-2000:]}")
    yield IMAGE
    docker("rmi", IMAGE)


@pytest.mark.usefixtures("image")
def test_image_runs_as_a_non_root_user() -> None:
    configured = docker("image", "inspect", "--format", "{{.Config.User}}", IMAGE)
    effective = run_in_image("id", "-u")

    assert configured.stdout.strip() == "10001:10001"
    assert effective.stdout.strip() == "10001"


@pytest.mark.usefixtures("image")
def test_image_has_no_uv() -> None:
    found = run_in_image(
        "sh",
        "-c",
        "command -v uv uvx || true; find / -xdev \\( -name uv -o -name uvx \\) -type f",
        user="0",
    )

    assert found.returncode == 0
    assert found.stdout.strip() == ""


@pytest.mark.usefixtures("image")
def test_image_has_no_dotenv_files() -> None:
    found = run_in_image("find", "/", "-xdev", "-name", ".env*", user="0")

    assert found.returncode == 0
    assert found.stdout.strip() == ""


@pytest.mark.usefixtures("image")
def test_image_has_no_development_dependencies() -> None:
    script = (
        "import importlib.util, shutil, sys;"
        f"present = [m for m in {DEV_MODULES} if importlib.util.find_spec(m)];"
        "present += [b for b in ('ruff', 'pytest', 'mypy') if shutil.which(b)];"
        "print(','.join(present)); sys.exit(1 if present else 0)"
    )
    result = run_in_image("python", "-c", script)

    assert result.returncode == 0
    assert result.stdout.strip() == ""


@pytest.mark.usefixtures("image")
def test_image_installs_the_package_and_alembic_without_the_source_tree() -> None:
    package = run_in_image("python", "-c", "import cce; print(cce.__file__)")
    alembic = run_in_image("alembic", "--version")
    tree = run_in_image("sh", "-c", "ls /app /app/apps/api")

    assert package.returncode == 0 and "site-packages" in package.stdout
    assert alembic.returncode == 0
    assert "src" not in tree.stdout.split()
    assert "tests" not in tree.stdout.split()

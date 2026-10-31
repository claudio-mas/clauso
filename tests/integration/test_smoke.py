"""A imagem constrói, a API sobe pelo Compose (projeto de teste) e as duas fases do smoke passam.

As chaves são emitidas pelo código de produção e gravadas no banco de teste, que é o mesmo do
Compose (`compose_targets_validated_database`). O smoke recebe as chaves pelo ambiente do processo,
nunca pelo argv, e sua saída é examinada antes de qualquer outra coisa.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator

import pytest
from db_support import PASSTHROUGH, ROOT
from seeding import SeededTenant, emit_key
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from cce.admin import bootstrap
from cce.shared.config import Settings
from cce.shared.db.session import tenant_session

from .support import assert_no_leaks, compose_env_file, read_dotenv, require, run_compose

pytestmark = [pytest.mark.integration, pytest.mark.sensitive]

BASE_URL = "http://127.0.0.1:8000"


def compose_secrets() -> dict[str, str]:
    dotenv = read_dotenv(compose_env_file())
    forbidden = {name: value for name, value in dotenv.items() if "PASSWORD" in name}
    forbidden["pepper map"] = dotenv.get("CCE_API_KEY_PEPPERS", "")
    return forbidden


@pytest.fixture
def compose_api(compose_targets_validated_database: None) -> Iterator[None]:
    build = run_compose("build", "api")
    assert_no_leaks(build.stdout + build.stderr, compose_secrets(), "the image build output")
    require(build.returncode == 0, "docker compose build api failed")
    try:
        up = run_compose("up", "-d", "--wait", "--no-build", "api")
        assert_no_leaks(up.stdout + up.stderr, compose_secrets(), "the compose up output")
        require(up.returncode == 0, "the api service did not become healthy through Compose")
        yield
    finally:
        logs = run_compose("logs", "--no-color", "api", "migrate")
        down = run_compose("rm", "--stop", "--force", "api", "migrate")
        assert_no_leaks(logs.stdout + logs.stderr, compose_secrets(), "the api container logs")
        require(down.returncode == 0, "the api and migrate containers were not removed")


def run_smoke(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke.py"), *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=environment,
        timeout=60,
    )


@pytest.mark.usefixtures("compose_api")
async def test_the_api_runs_through_compose_and_the_two_tenant_smoke_passes(
    migrator_engine: AsyncEngine,
    db_settings: Settings,
    tenant_a: SeededTenant,
    tenant_b: SeededTenant,
) -> None:
    """As duas fases do smoke, com a revogação entre elas feita pelo código de produção da CLI."""
    _, key_a = await emit_key(migrator_engine, db_settings, tenant_a)
    _, key_b = await emit_key(migrator_engine, db_settings, tenant_b)
    _, read_only = await emit_key(migrator_engine, db_settings, tenant_a, scopes=["tenant:read"])
    _, revocable = await emit_key(migrator_engine, db_settings, tenant_a)
    keys = {
        "key A": key_a.raw_key,
        "key B": key_b.raw_key,
        "read-only key": read_only.raw_key,
        "revocable key": revocable.raw_key,
    }
    environment = {name: os.environ[name] for name in PASSTHROUGH if name in os.environ}
    environment |= {
        "CCE_SMOKE_BASE_URL": BASE_URL,
        "CCE_SMOKE_KEY_A": key_a.raw_key.get_secret_value(),
        "CCE_SMOKE_KEY_B": key_b.raw_key.get_secret_value(),
        "CCE_SMOKE_KEY_READ_ONLY": read_only.raw_key.get_secret_value(),
        "CCE_SMOKE_KEY_REVOCABLE": revocable.raw_key.get_secret_value(),
    }

    active = await asyncio.to_thread(run_smoke, environment)
    output = active.stdout + active.stderr
    assert_no_leaks(output, {**keys, **compose_secrets()}, "smoke")
    require(active.returncode == 0, f"the smoke failed:\n{output[-2000:]}")
    require("smoke passed" in active.stdout, "the smoke did not report success")

    factory = async_sessionmaker(migrator_engine, expire_on_commit=False)
    async with tenant_session(factory, tenant_a.id) as session:
        revoked = await bootstrap.revoke_api_key(session, tenant_a.id, revocable.key_id)
    require(revoked, "the revocable key was not revoked")

    after = await asyncio.to_thread(run_smoke, environment, "--revoked")
    output = after.stdout + after.stderr
    assert_no_leaks(output, {**keys, **compose_secrets()}, "smoke --revoked")
    require(after.returncode == 0, f"the revoked phase failed:\n{output[-2000:]}")
    require("smoke passed" in after.stdout, "the revoked phase did not report success")

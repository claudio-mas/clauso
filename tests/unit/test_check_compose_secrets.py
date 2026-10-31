"""`scripts/check_compose_secrets.py` confere os segredos da composição da API sem exibi-los.

Cada teste usa sentinelas no lugar das senhas e dos peppers e examina toda a saída do script, no
sucesso e em cada falha: o script só pode citar serviços e nomes de variável.
"""

from __future__ import annotations

import base64
import copy
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from integration.support import assert_no_leaks, require

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_compose_secrets  # noqa: E402

ADMIN = "admin-SENTINEL-" + "a" * 40
APP = "app-SENTINEL-" + "b" * 40
MIGRATOR = "migrator-SENTINEL-" + "c" * 40
PEPPER = base64.b64encode(b"pepper-SENTINEL-" + b"p" * 32).decode()
SECRETS = {"admin password": ADMIN, "app password": APP, "migrator password": MIGRATOR}
SECRETS_AND_PEPPER = {**SECRETS, "pepper": PEPPER}


def dsn(role: str, password: str) -> str:
    return f"postgresql+asyncpg://{role}:{password}@postgres:5432/cce_test_ci"


def rendered() -> dict[str, Any]:
    """O que `docker compose config --format json` devolve para um dotenv válido de CI."""
    return {
        "name": "cce-ci",
        "services": {
            "postgres": {
                "environment": {
                    "POSTGRES_DB": "cce_test_ci",
                    "POSTGRES_USER": "postgres",
                    "POSTGRES_PASSWORD": ADMIN,
                    "CCE_APP_DB_PASSWORD": APP,
                    "CCE_MIGRATOR_DB_PASSWORD": MIGRATOR,
                }
            },
            "redis": {},
            "migrate": {
                "environment": {
                    "CCE_ENV": "test",
                    "CCE_MIGRATOR_DATABASE_URL": dsn("cce_migrator", MIGRATOR),
                }
            },
            "api": {
                "environment": {
                    "CCE_ENV": "test",
                    "CCE_VERSION": "0.1.0",
                    "CCE_LOG_LEVEL": "INFO",
                    "CCE_LOG_FORMAT": "json",
                    "CCE_DATABASE_URL": dsn("cce_app", APP),
                    "CCE_REDIS_URL": "redis://redis:6379/0",
                    "CCE_ALLOWED_ORIGINS": "[]",
                    "CCE_API_KEY_PEPPERS": json.dumps({"1": PEPPER}),
                    "CCE_API_KEY_ACTIVE_PEPPER_VERSION": "1",
                    "CCE_API_KEY_TTL_DAYS": "90",
                    "CCE_DOCS_ENABLED": "true",
                }
            },
        },
    }


def api_environment(config: dict[str, Any]) -> dict[str, Any]:
    environment: dict[str, Any] = config["services"]["api"]["environment"]
    return environment


def postgres_environment(config: dict[str, Any]) -> dict[str, Any]:
    environment: dict[str, Any] = config["services"]["postgres"]["environment"]
    return environment


def check(config: dict[str, Any]) -> list[str]:
    problems = check_compose_secrets.problems(config)
    assert_no_leaks("\n".join(problems), SECRETS_AND_PEPPER, "the problems")
    return problems


def test_a_valid_composition_has_no_problem() -> None:
    require(check(rendered()) == [], "a valid composition was reported as a problem")


def leak_migrator_url(config: dict[str, Any]) -> None:
    api_environment(config)["CCE_MIGRATOR_DATABASE_URL"] = dsn("cce_migrator", MIGRATOR)


def leak_admin_password(config: dict[str, Any]) -> None:
    api_environment(config)["POSTGRES_PASSWORD"] = ADMIN


def leak_admin_password_elsewhere(config: dict[str, Any]) -> None:
    api_environment(config)["CCE_SOMETHING"] = f"x{ADMIN}x"


def leak_migrator_password(config: dict[str, Any]) -> None:
    config["services"]["api"]["command"] = ["echo", MIGRATOR]


def runtime_as_migrator(config: dict[str, Any]) -> None:
    api_environment(config)["CCE_DATABASE_URL"] = dsn("cce_migrator", APP)


def api_password_differs(config: dict[str, Any]) -> None:
    api_environment(config)["CCE_DATABASE_URL"] = dsn("cce_app", "d" * 48)


def short_pepper(config: dict[str, Any]) -> None:
    short = base64.b64encode(b"short").decode()
    api_environment(config)["CCE_API_KEY_PEPPERS"] = json.dumps({"1": short})


def no_pepper(config: dict[str, Any]) -> None:
    del api_environment(config)["CCE_API_KEY_PEPPERS"]


def placeholder_password(config: dict[str, Any]) -> None:
    postgres_environment(config)["CCE_APP_DB_PASSWORD"] = "__APP_PASSWORD__"
    api_environment(config)["CCE_DATABASE_URL"] = dsn("cce_app", "__APP_PASSWORD__")


def short_password(config: dict[str, Any]) -> None:
    postgres_environment(config)["POSTGRES_PASSWORD"] = "short"


def empty_password(config: dict[str, Any]) -> None:
    postgres_environment(config)["CCE_MIGRATOR_DB_PASSWORD"] = ""


def reused_password(config: dict[str, Any]) -> None:
    postgres_environment(config)["CCE_MIGRATOR_DB_PASSWORD"] = APP


def no_api_service(config: dict[str, Any]) -> None:
    del config["services"]["api"]


DEFECTS: dict[str, tuple[Callable[[dict[str, Any]], None], str]] = {
    "migrator URL in api": (
        leak_migrator_url,
        "the api service receives CCE_MIGRATOR_DATABASE_URL",
    ),
    "admin password variable in api": (leak_admin_password, "receives POSTGRES_PASSWORD"),
    "admin password value in api": (leak_admin_password_elsewhere, "the postgres admin password"),
    "migrator password value in api": (leak_migrator_password, "the cce_migrator password"),
    "runtime DSN as migrator": (runtime_as_migrator, "does not connect as cce_app"),
    "api password differs": (api_password_differs, "is not the cce_app password"),
    "short pepper": (short_pepper, "CCE_API_KEY_PEPPERS"),
    "no pepper": (no_pepper, "CCE_API_KEY_PEPPERS"),
    "placeholder password": (placeholder_password, "CCE_APP_DB_PASSWORD is a placeholder"),
    "short password": (short_password, "POSTGRES_PASSWORD is shorter than"),
    "empty password": (empty_password, "CCE_MIGRATOR_DB_PASSWORD is empty"),
    "reused password": (reused_password, "share a value"),
    "no api service": (no_api_service, "has no api service"),
}


@pytest.mark.parametrize("defect", list(DEFECTS))
def test_each_defect_is_reported_by_name_only(defect: str) -> None:
    alter, expected = DEFECTS[defect]
    config = copy.deepcopy(rendered())
    alter(config)

    problems = check(config)

    require(any(expected in problem for problem in problems), f"{defect} was not reported")


def fake_compose(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str, stderr: str = ""
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **options})
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def run_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config_json: str, code: int
) -> tuple[int, str, list[dict[str, Any]]]:
    calls = fake_compose(monkeypatch, code, config_json, f"compose said {ADMIN}")
    exit_code = check_compose_secrets.main(["--env-file", "x.env", "--project-name", "cce-ci"])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert_no_leaks(output, SECRETS_AND_PEPPER, "the script output")
    return exit_code, output, calls


def test_main_passes_a_valid_composition_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code, output, calls = run_main(monkeypatch, capsys, json.dumps(rendered()), 0)

    require(exit_code == 0, "a valid composition did not pass")
    require("compose secrets check passed" in output, "the success line is missing")
    argv = calls[0]["argv"]
    require(argv[:2] == ["docker", "compose"], "the script does not run docker compose")
    require(argv[argv.index("-p") + 1] == "cce-ci", "the project name is not passed through")
    require(argv[-3:] == ["config", "--format", "json"], "the script does not render the config")
    require(calls[0]["capture_output"] is True, "the rendered config is not captured")
    environment = calls[0]["env"]
    require(
        not any(name.upper().startswith("CCE_") for name in environment),
        "CCE_* variables of the process would override the dotenv",
    )


def test_main_fails_with_one_line_per_problem(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = rendered()
    leak_migrator_url(config)
    short_pepper(config)

    exit_code, output, _ = run_main(monkeypatch, capsys, json.dumps(config), 0)

    require(exit_code == 1, "a leaking composition did not fail with code 1")
    require(output.count("compose secrets: ") >= 2, "not one line per problem")


@pytest.mark.parametrize(("stdout", "code"), [("", 1), ("not json", 0), ("[]", 0)])
def test_main_withholds_the_compose_output_when_it_cannot_render(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdout: str, code: int
) -> None:
    exit_code, output, _ = run_main(monkeypatch, capsys, stdout, code)

    require(exit_code == 2, "an unrenderable composition did not fail with code 2")
    require("compose said" not in output, "the compose stderr was repeated")


@pytest.mark.integration
@pytest.mark.sensitive
def test_the_repository_composition_rendered_with_generated_secrets_passes(
    tmp_path: Path,
) -> None:
    """O Compose real com um dotenv novo de `init_env.py` (só `config`, sem containers)."""
    dotenv = tmp_path / ".env.ci"
    generated = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "init_env.py"),
            "--target",
            str(dotenv),
            "--env",
            "test",
            "--database-name",
            "cce_test_ci",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    require(generated.returncode == 0, "init_env.py could not generate the dotenv")
    values = dict(line.partition("=")[::2] for line in dotenv.read_text().splitlines())
    forbidden = {
        name: value.strip("'")
        for name, value in values.items()
        if "PASSWORD" in name or name == "CCE_API_KEY_PEPPERS"
    }

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "check_compose_secrets.py"),
            "--env-file",
            str(dotenv),
            "--project-name",
            "cce-ci",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )

    output = result.stdout + result.stderr
    assert_no_leaks(output, forbidden, "the script output")
    require(result.returncode == 0, f"the generated composition failed:\n{output[-2000:]}")


def test_the_process_environment_never_fills_a_variable_the_api_service_lacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validação usa só o ambiente renderizado do serviço, não o de quem roda o script."""
    monkeypatch.setenv("CCE_API_KEY_PEPPERS", json.dumps({"1": PEPPER}))
    config = rendered()
    no_pepper(config)

    problems = check(config)

    require(any("CCE_API_KEY_PEPPERS" in problem for problem in problems), "pepper not reported")

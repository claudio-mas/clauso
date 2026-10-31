#!/usr/bin/env bash
# Verificação obrigatória da Foundation: reproduz o ambiente do zero no projeto Compose descartável
# `cce-ci` e termina com código diferente de zero no primeiro controle que falhar. É o que o job
# `foundation` da CI roda em toda Pull Request, e roda igual localmente, a partir da raiz:
#
#     bash scripts/ci.sh
#
# Pré-requisitos: Docker com Compose v2, uv 0.12.10 (ADR-0011) e as portas 5432, 6379 e 8000 de
# loopback livres (os projetos `cce` e `cce-test` não podem estar de pé ao mesmo tempo).
#
# Segredos: o dotenv é gerado com segredos novos num diretório temporário (`init_env.py`), nunca é
# impresso e é apagado no fim. Nenhuma etapa ecoa valores, e as chaves do smoke vão pelo ambiente
# do processo, nunca pelo argv. O teardown apaga containers e volumes SÓ do projeto `cce-ci` criado
# aqui; não é a forma de limpar os ambientes `cce` e `cce-test` de quem desenvolve.
set -euo pipefail

readonly UV_VERSION="0.12.10"
readonly PROJECT="cce-ci"
readonly DATABASE="cce_test_ci"
readonly BASE_URL="http://127.0.0.1:8000"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT

step() {
    printf '\n==> %s\n' "$1"
}

fail() {
    printf 'ci: %s\n' "$1" >&2
    exit 1
}

# A única chamada ao Compose: sempre o dotenv gerado e o projeto descartável.
compose() {
    docker compose --env-file "$env_file" -p "$PROJECT" --project-directory "$ROOT" \
        -f "$ROOT/docker-compose.yml" "$@"
}

uv_run() {
    uv run --project apps/api --locked "$@"
}

# Sai com o status do pipeline; um teardown que falha nunca deixa a execução verde.
teardown() {
    local status=$?
    trap - EXIT
    if [[ -f "$env_file" ]] \
        && ! compose down --volumes --remove-orphans --timeout 10 >/dev/null 2>&1; then
        printf 'ci: the teardown of the %s project failed; remove it before the next run\n' \
            "$PROJECT" >&2
        if ((status == 0)); then
            status=1
        fi
    fi
    rm -rf "$work_dir"
    exit "$status"
}

# Carregado com `source` (os testes de tests/unit/test_ci_pipeline.py), o script só define as
# funções acima; nada abaixo roda.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 0
fi

cd "$ROOT"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/cce-ci.XXXXXX")"
readonly work_dir
readonly env_file="$work_dir/.env.ci"
trap teardown EXIT

# Um projeto `cce-ci` que sobrou de uma execução interrompida tem volumes com senhas antigas.
leftovers="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")"
leftovers+="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")"
if [[ -n "$leftovers" ]]; then
    fail "the $PROJECT project is left over from an interrupted run; remove its containers and volumes first"
fi

step "uv $UV_VERSION"
found_uv="$(uv --version | awk '{print $2}')"
[[ "$found_uv" == "$UV_VERSION" ]] || fail "uv $UV_VERSION is required (found $found_uv)"

step "disposable dotenv ($PROJECT, database $DATABASE)"
python3 scripts/init_env.py --target "$env_file" --env test --database-name "$DATABASE"
export CCE_ENV=test
export CCE_ENV_FILE="$env_file"
export CCE_TEST_COMPOSE_PROJECT="$PROJECT"
# Os dados de cobertura ficam fora do repositório e não se misturam com os de outra execução.
export COVERAGE_FILE="$work_dir/.coverage"

step "Python 3.12 and locked dependencies"
uv python install 3.12
uv lock --project apps/api --check
uv sync --project apps/api --locked --group dev

step "lint and types"
uv_run ruff check --config apps/api/pyproject.toml apps/api/src tests scripts
uv_run ruff format --check --config apps/api/pyproject.toml apps/api/src tests scripts
uv_run mypy --config-file apps/api/pyproject.toml apps/api/src tests scripts

step "Compose configuration and API secrets (values are never printed)"
compose config --quiet
uv_run python scripts/check_compose_secrets.py --env-file "$env_file" --project-name "$PROJECT"

step "PostgreSQL and Redis"
compose up -d --wait postgres redis

step "migrations to head"
uv_run alembic -c alembic.ini upgrade head

step "migration tests"
uv_run pytest tests/migrations -v

step "unit, integration and contract tests with coverage"
uv_run pytest tests/unit tests/integration tests/contracts --cov=cce \
    --cov-config=apps/api/pyproject.toml --cov-report=term --cov-report=xml

step "alembic check and schema check"
uv_run alembic -c alembic.ini check
uv_run python scripts/check_schema.py

step "dependency audit"
uv_run pip-audit --path apps/api/.venv/lib/python3.12/site-packages

step "image, separate migration job and API through Compose"
compose build api
compose run --rm --no-deps migrate
compose up -d --wait --no-build --no-deps api

step "two-Tenant smoke over HTTP"
# Cria um Tenant e devolve só a chave inicial; o JSON da CLI nunca chega ao terminal.
initial_key() {
    uv_run cce-admin tenant create --name "$1" --slug "$2" \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["api_key"]["key"])'
}
suffix="$(date +%s)"
key_a="$(initial_key "CI Smoke A" "ci-smoke-a-$suffix")"
key_b="$(initial_key "CI Smoke B" "ci-smoke-b-$suffix")"
CCE_SMOKE_BASE_URL="$BASE_URL" CCE_SMOKE_KEY_A="$key_a" CCE_SMOKE_KEY_B="$key_b" \
    python3 scripts/smoke.py
unset key_a key_b

step "report"
total="$(uv_run coverage report --data-file "$COVERAGE_FILE" --format=total)"
printf 'ci: coverage %s%% (minimum 85%%, apps/api/pyproject.toml)\n' "$total"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf '### Foundation\n\nAll mandatory controls passed. Coverage: %s%%.\n' "$total" \
        >>"$GITHUB_STEP_SUMMARY"
fi
printf '\nci: all mandatory controls passed\n'

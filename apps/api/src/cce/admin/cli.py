"""CLI administrativa `cce-admin`: criar Tenants e chaves, revogar chaves, suspender e ativar.

Roda como `cce_migrator` (`AdminSettings`), fora da API, e nunca é uma forma de autenticação HTTP.

Contrato de saída:

* stdout: um único objeto JSON por comando. A chave bruta aparece somente em `tenant create` e em
  `api-key issue`, uma vez, depois do commit. Se o commit falha, nada é impresso em stdout.
* stderr: um único objeto JSON `{"error": ..., "kind": ...}` com texto fixo; nunca o DSN, o texto de
  uma exceção do driver, um traceback nem o valor recebido numa opção.
* códigos de saída: 0 sucesso; 1 operação recusada (não encontrado, slug em uso, pedido inválido);
  2 uso ou configuração inválidos; 3 banco indisponível ou falha inesperada.

Todas as opções são texto e validadas aqui, com mensagens que citam a opção e nunca o valor: quem
cola uma chave bruta no lugar de um id não a vê repetida (o Click ecoaria o valor numa conversão de
tipo ou num argumento extra).
"""

import asyncio
import json
import sys
from collections.abc import Callable, Coroutine, Sequence
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import typer
from pydantic import ValidationError

from cce.admin import bootstrap
from cce.identity.tenants.schemas import TenantCreate
from cce.integrations.api_keys.scopes import ALLOWED_SCOPES
from cce.integrations.api_keys.service import ProvisionedKey
from cce.shared.config import AdminSettings, ConfigurationError, load_admin_settings
from cce.shared.db.engine import build_migration_engine, build_session_factory
from cce.shared.db.session import tenant_session
from cce.shared.ids import decode_key_id, decode_tenant_id, encode_key_id, encode_tenant_id

EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3

app = typer.Typer(
    name="cce-admin",
    help="Administração do Commercial Closing Engine: Tenants e chaves de API.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
tenant_app = typer.Typer(help="Criar, suspender e ativar Tenants.", no_args_is_help=True)
key_app = typer.Typer(help="Emitir e revogar chaves de API.", no_args_is_help=True)
app.add_typer(tenant_app, name="tenant")
app.add_typer(key_app, name="api-key")


# --- validação das opções (texto fixo, sem eco do valor) -------------------------------------


def _tenant_option(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        decode_tenant_id(value)
    except ValueError:
        raise typer.BadParameter("must be ten_ followed by 32 lowercase hex digits") from None
    return value


def _key_option(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        decode_key_id(value)
    except ValueError:
        raise typer.BadParameter("must be key_ followed by 32 lowercase hex digits") from None
    return value


def _ttl_option(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 365:
        raise typer.BadParameter("must be a whole number of days between 1 and 365")
    return value


def _label_option(value: str | None) -> str | None:
    if value is not None and not 1 <= len(value.strip()) <= 100:
        raise typer.BadParameter("must have 1 to 100 characters")
    return value


def _scope_option(values: list[str] | None) -> list[str] | None:
    if values is not None and any(value not in ALLOWED_SCOPES for value in values):
        raise typer.BadParameter(f"must be one of: {', '.join(ALLOWED_SCOPES)}")
    return values


TenantOption = Annotated[
    str, typer.Option("--tenant", help="ten_<id> do Tenant.", callback=_tenant_option)
]
KeyOption = Annotated[str, typer.Option("--key", help="key_<id> da chave.", callback=_key_option)]


# --- execução --------------------------------------------------------------------------------


def _fail(message: str, kind: str, code: int) -> typer.Exit:
    typer.echo(json.dumps({"error": message, "kind": kind}), err=True)
    return typer.Exit(code)


def _run[T](operation: Callable[[AdminSettings], Coroutine[Any, Any, T]]) -> T:
    """Carrega a configuração, executa e traduz toda falha em código de saída e mensagem fixa."""
    try:
        settings = load_admin_settings()
    except ConfigurationError as error:  # cita variáveis, nunca valores
        raise _fail(str(error), "configuration", EXIT_USAGE) from None
    try:
        return asyncio.run(operation(settings))
    except bootstrap.BootstrapError as error:  # mensagens fixas, escritas neste pacote
        raise _fail(str(error), "refused", EXIT_REFUSED) from None
    except Exception as error:  # driver, rede ou defeito: só o tipo sai, nunca a mensagem
        raise _fail(
            "the operation could not be completed", type(error).__name__, EXIT_UNAVAILABLE
        ) from None


async def _with_factory[T](
    settings: AdminSettings, operation: Callable[[Any], Coroutine[Any, Any, T]]
) -> T:
    engine = build_migration_engine(settings)
    try:
        return await operation(build_session_factory(engine))
    finally:
        await engine.dispose()


def _emit(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload))


def _iso(value: datetime) -> str:
    return value.isoformat()


def _key_payload(key: ProvisionedKey) -> dict[str, Any]:
    """Único lugar que escreve a chave bruta; só é chamado depois do commit."""
    return {
        "id": encode_key_id(key.issued.key_id),
        "key": key.issued.raw_key.get_secret_value(),
        "label": key.label,
        "scopes": list(key.scopes),
        "expires_at": _iso(key.expires_at),
    }


def _tenant_id(value: str) -> UUID:
    return decode_tenant_id(value)


# --- tenant ----------------------------------------------------------------------------------


@tenant_app.command("create")
def tenant_create(
    name: Annotated[str, typer.Option("--name", help="Nome da empresa.")],
    slug: Annotated[str, typer.Option("--slug", help="Identificador curto único (a-z, 0-9, -).")],
    plan: Annotated[str, typer.Option("--plan")] = "development",
    currency: Annotated[str, typer.Option("--currency")] = "BRL",
    timezone: Annotated[str, typer.Option("--timezone")] = "America/Sao_Paulo",
    locale: Annotated[str, typer.Option("--locale")] = "pt-BR",
) -> None:
    """Cria o Tenant e a chave inicial (todos os scopes) na mesma transação."""
    try:
        command = TenantCreate.model_validate(
            {
                "name": name,
                "slug": slug,
                "plan": plan,
                "default_currency": currency,
                "timezone": timezone,
                "locale": locale,
            }
        )
    except ValidationError as error:
        fields = sorted({str(item["loc"][0]) for item in error.errors() if item["loc"]})
        raise _fail("invalid value for: " + ", ".join(fields), "usage", EXIT_USAGE) from None

    async def operation(settings: AdminSettings) -> bootstrap.ProvisionedTenant:
        return await _with_factory(
            settings, lambda factory: bootstrap.provision_tenant(factory, settings, command)
        )

    provisioned = _run(operation)
    _emit(
        {
            "tenant": {
                "id": encode_tenant_id(provisioned.tenant_id),
                "name": provisioned.name,
                "slug": provisioned.slug,
                "status": provisioned.status,
                "plan": provisioned.plan,
                "default_currency": provisioned.default_currency,
                "timezone": provisioned.timezone,
                "locale": provisioned.locale,
            },
            "api_key": _key_payload(provisioned.api_key),
        }
    )


def _set_status(tenant: str, status: str) -> None:
    tenant_id = _tenant_id(tenant)

    async def change(factory: Any) -> None:
        async with tenant_session(factory, tenant_id) as session:
            await bootstrap.set_tenant_status(session, tenant_id, status)

    async def operation(settings: AdminSettings) -> None:
        await _with_factory(settings, change)

    _run(operation)
    _emit({"tenant_id": encode_tenant_id(tenant_id), "status": status})


@tenant_app.command("suspend")
def tenant_suspend(tenant: TenantOption) -> None:
    """Suspende o Tenant: nenhuma de suas chaves autentica a partir da próxima requisição."""
    _set_status(tenant, "suspended")


@tenant_app.command("activate")
def tenant_activate(tenant: TenantOption) -> None:
    """Reativa um Tenant suspenso."""
    _set_status(tenant, "active")


# --- api-key ---------------------------------------------------------------------------------


@key_app.command("issue")
def api_key_issue(
    tenant: TenantOption,
    label: Annotated[
        str, typer.Option("--label", help="Para que serve a chave.", callback=_label_option)
    ],
    scope: Annotated[
        list[str],
        typer.Option("--scope", help="Scope concedido; repita a opção.", callback=_scope_option),
    ],
    ttl_days: Annotated[
        str | None,
        typer.Option("--ttl-days", help="Validade em dias (1 a 365).", callback=_ttl_option),
    ] = None,
) -> None:
    """Emite uma chave para o Tenant. Ela é mostrada uma única vez, depois do commit."""
    tenant_id = _tenant_id(tenant)

    async def issue(settings: AdminSettings, factory: Any) -> ProvisionedKey:
        days = int(ttl_days) if ttl_days is not None else settings.api_key_ttl_days
        async with tenant_session(factory, tenant_id) as session:
            key = await bootstrap.issue_api_key(session, tenant_id, label, scope, days, settings)
        return key

    async def operation(settings: AdminSettings) -> ProvisionedKey:
        return await _with_factory(settings, lambda factory: issue(settings, factory))

    key = _run(operation)
    _emit({"tenant_id": encode_tenant_id(tenant_id), "api_key": _key_payload(key)})


@key_app.command("revoke")
def api_key_revoke(tenant: TenantOption, key: KeyOption) -> None:
    """Revoga a chave do Tenant (repetir é um sucesso). A chave de outro Tenant é recusada."""
    tenant_id, key_id = _tenant_id(tenant), decode_key_id(key)

    async def revoke(factory: Any) -> bool:
        async with tenant_session(factory, tenant_id) as session:
            return await bootstrap.revoke_api_key(session, tenant_id, key_id)

    async def operation(settings: AdminSettings) -> bool:
        return await _with_factory(settings, revoke)

    if not _run(operation):
        raise _fail("api key not found for this tenant", "refused", EXIT_REFUSED)
    _emit(
        {"tenant_id": encode_tenant_id(tenant_id), "key_id": encode_key_id(key_id), "revoked": True}
    )


# --- ponto de entrada ------------------------------------------------------------------------


def _kinds(error: BaseException) -> set[str]:
    # O Typer embute o Click (`typer._click`, privado), então as classes de erro de uso são
    # reconhecidas pelo nome em vez de importadas.
    return {kind.__name__ for kind in type(error).__mro__}


def _usage_message(error: Exception) -> str:
    """Mensagem de uso sem eco de valores digitados."""
    kinds = _kinds(error)
    message = str(error.format_message()) if hasattr(error, "format_message") else "usage error"
    if "NoSuchOption" in kinds:
        return "unknown option"
    if "BadParameter" in kinds or "MissingParameter" in kinds:
        return message
    if "extra argument" in message:
        return "unexpected extra argument"
    if message.startswith("No such command"):
        return "unknown command"
    return message


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = app(args=list(argv) if argv is not None else None, standalone_mode=False)
    except typer.Exit as request:
        return request.exit_code
    except typer.Abort:
        return EXIT_REFUSED
    except Exception as error:
        kinds = _kinds(error)
        if "NoArgsIsHelpError" in kinds:
            typer.echo(error.ctx.get_help())  # type: ignore[attr-defined]
            return EXIT_USAGE
        if "UsageError" not in kinds:
            raise
        typer.echo(json.dumps({"error": _usage_message(error), "kind": "usage"}), err=True)
        return EXIT_USAGE
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())

"""Application factory da API (plano do M0, seção 12.1).

`create_app` só monta a aplicação: nada conecta na importação nem na criação. O lifespan cria o
engine do runtime, a fábrica de sessões, o cliente Redis e os heads esperados do Alembic, guarda
tudo em `app.state` e, no shutdown, fecha o Redis e descarta o engine, inclusive depois de um
startup que falhou no meio. Migrations nunca rodam aqui.

Swagger e OpenAPI só existem com `CCE_DOCS_ENABLED`, que a configuração recusa em staging e
production. CORS só para as origins explícitas de `CCE_ALLOWED_ORIGINS`, sem credenciais; sem
origins configuradas não há cabeçalho CORS algum.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from cce.api.errors import install_error_handlers
from cce.api.health import load_expected_heads
from cce.api.health import router as health_router
from cce.api.middleware import REQUEST_ID_HEADER, TRACE_ID_HEADER, RequestContextMiddleware
from cce.api.openapi import install_openapi
from cce.api.router import api_router
from cce.shared.config import Settings, load_settings
from cce.shared.db.engine import build_engine, build_session_factory
from cce.shared.logging import configure_logging
from cce.shared.redis import build_redis

TITLE = "Commercial Closing Engine API"
CORS_METHODS = ("GET", "PATCH")
CORS_HEADERS = ("Authorization", "Content-Type")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            engine = build_engine(settings)
            stack.push_async_callback(engine.dispose)
            redis = build_redis(settings)
            stack.push_async_callback(redis.aclose)
            app.state.engine = engine
            app.state.session_factory = build_session_factory(engine)
            app.state.redis = redis
            app.state.expected_heads = load_expected_heads()
            yield

    docs = settings.docs_enabled
    app = FastAPI(
        title=TITLE,
        version=settings.version,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        swagger_ui_oauth2_redirect_url="/docs/oauth2-redirect" if docs else None,
    )
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    install_openapi(app)
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=False,
            allow_methods=list(CORS_METHODS),
            allow_headers=list(CORS_HEADERS),
            expose_headers=[REQUEST_ID_HEADER, TRACE_ID_HEADER],
        )
    # Adicionado por último, fica por fora de tudo: até a resposta do CORS leva os IDs.
    app.add_middleware(RequestContextMiddleware)
    return app

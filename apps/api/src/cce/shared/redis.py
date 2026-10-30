"""Cliente Redis do processo (ADR-0015). Um por processo, criado e fechado pelo lifespan da API.

Criar o cliente não conecta: a primeira conexão é lazy. No M0 o Redis só serve à readiness; ele
nunca é fonte de verdade de estado comercial nem de autorização, e nada guarda chave bruta nele.
"""

from typing import Any

from redis.asyncio import Redis

from cce.shared.config import Settings

CONNECT_TIMEOUT_SECONDS = 1
SOCKET_TIMEOUT_SECONDS = 1


def build_redis(settings: Settings) -> Redis:
    options: dict[str, Any] = {
        "socket_connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": SOCKET_TIMEOUT_SECONDS,
        "decode_responses": True,
    }
    url = settings.redis_url.get_secret_value()
    if url.startswith("rediss://"):
        # Staging e production: certificado e hostname sempre verificados.
        options["ssl_cert_reqs"] = "required"
        options["ssl_check_hostname"] = True
        if settings.redis_ca_file is not None:
            options["ssl_ca_certs"] = str(settings.redis_ca_file)
    return Redis.from_url(url, **options)

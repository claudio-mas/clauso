from __future__ import annotations

from collections.abc import Awaitable
from typing import cast

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from cce.shared.config import Settings

pytestmark = pytest.mark.integration


async def test_postgres_is_version_17(app_connection: AsyncConnection) -> None:
    version = (await app_connection.execute(text("show server_version_num"))).scalar_one()

    assert str(version).startswith("17")


async def test_redis_answers_ping_and_is_version_7_4(db_settings: Settings) -> None:
    client = Redis.from_url(db_settings.redis_url.get_secret_value())
    try:
        assert await cast("Awaitable[bool]", client.ping()) is True
        info = await client.info("server")
    finally:
        await client.aclose()

    assert str(info["redis_version"]).startswith("7.4.")

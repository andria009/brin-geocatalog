from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from geocatalog.config import get_settings


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        yield conn
    finally:
        await conn.close()


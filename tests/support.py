from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection, sql
from psycopg.conninfo import make_conninfo

from vocab.db import DEFAULT_DATABASE_URL, Database


@asynccontextmanager
async def temporary_database() -> AsyncIterator[Database]:
    base_dsn = os.environ.get("TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    name = f"words_trainer_test_{uuid.uuid4().hex}"
    admin_dsn = make_conninfo(base_dsn, dbname="postgres")
    admin = await AsyncConnection.connect(admin_dsn, autocommit=True)
    await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    database = Database(make_conninfo(base_dsn, dbname=name), open_timeout=10)
    try:
        await database.open()
        yield database
    finally:
        await database.close()
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        await admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        await admin.close()

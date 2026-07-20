"""Atomic per-learner LLM budget reservations and bounded chat history."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from . import db


class BudgetExceeded(RuntimeError):
    pass


async def reserve(
    database: db.Database, user_id: int, kind: str, amount_usd: Decimal
) -> int:
    month = date.today().replace(day=1)
    async with database.connection() as conn:
        async with conn.transaction():
            user_result = await conn.execute(
                "SELECT llm_monthly_cap_usd FROM users WHERE id = %s FOR UPDATE", (user_id,)
            )
            user = await user_result.fetchone()
            if not user:
                raise LookupError("user not found")
            usage = await db.fetch_one(
                conn,
                """SELECT coalesce(sum(CASE WHEN status = 'reconciled' THEN actual_usd
                                            WHEN status = 'reserved' THEN reserved_usd
                                            ELSE 0 END), 0) AS spent
                   FROM llm_usage WHERE user_id = %s AND month = %s""",
                (user_id, month),
            )
            if Decimal(usage["spent"]) + amount_usd > Decimal(user["llm_monthly_cap_usd"]):
                raise BudgetExceeded("monthly LLM budget reached")
            result = await conn.execute(
                """INSERT INTO llm_usage(user_id, month, kind, status, reserved_usd)
                   VALUES (%s, %s, %s, 'reserved', %s) RETURNING id""",
                (user_id, month, kind, amount_usd),
            )
            return (await result.fetchone())["id"]


async def reconcile(
    database: db.Database,
    usage_id: int,
    *,
    input_tokens: int,
    output_tokens: int,
    actual_usd: Decimal,
) -> None:
    async with database.connection() as conn:
        await conn.execute(
            """UPDATE llm_usage SET status = 'reconciled', actual_usd = %s,
                      input_tokens = %s, output_tokens = %s, reconciled_at = now()
               WHERE id = %s AND status = 'reserved'""",
            (actual_usd, input_tokens, output_tokens, usage_id),
        )


async def release(database: db.Database, usage_id: int) -> None:
    async with database.connection() as conn:
        await conn.execute(
            """UPDATE llm_usage SET status = 'released', reconciled_at = now()
               WHERE id = %s AND status = 'reserved'""",
            (usage_id,),
        )


async def append_chat(database: db.Database, user_id: int, role: str, content: str) -> None:
    async with database.connection() as conn:
        await conn.execute(
            "INSERT INTO chat_messages(user_id, role, content) VALUES (%s, %s, %s)",
            (user_id, role, content),
        )


async def chat_history(
    database: db.Database, user_id: int, *, limit: int = 20
) -> list[dict[str, Any]]:
    async with database.connection() as conn:
        rows = await db.fetch_all(
            conn,
            """SELECT role, content FROM (
                   SELECT id, role, content FROM chat_messages
                   WHERE user_id = %s ORDER BY id DESC LIMIT %s
               ) recent ORDER BY id""",
            (user_id, limit),
        )
    return [{"role": row["role"], "content": row["content"]} for row in rows]

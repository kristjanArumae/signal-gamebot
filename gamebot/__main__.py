"""Entry point: python -m gamebot"""

import asyncio
import logging
import os

from .bot import GameBot
from .llm import MODEL, Claude
from .signal_client import SignalClient
from .store import Store

log = logging.getLogger("gamebot")


async def scheduler(bot: GameBot, client: SignalClient) -> None:
    while True:
        try:
            await bot.tick()
            for group in await client.sync_groups():
                # Record each group's name so its data can be told apart (e.g. a test group vs the real one).
                bot.store.upsert_group(group["id"], group.get("name") or "", bot.clock())
        except Exception:
            log.exception("scheduled work failed")
        await asyncio.sleep(30)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    number = os.environ.get("BOT_NUMBER", "").strip()
    while not number:
        # Not registered yet: idle instead of crash-looping until BOT_NUMBER is configured.
        log.warning("BOT_NUMBER is not set; idling")
        await asyncio.sleep(3600)

    store = Store(os.environ.get("DB_PATH", "/data/gamebot.db"))
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    llm = Claude(
        api_key,
        monthly_budget_usd=float(os.environ.get("AI_MONTHLY_BUDGET_USD", "15")),
        model=os.environ.get("AI_MODEL", MODEL),
        record_usage=store.record_usage,
    ) if api_key else None
    log.info("AI helpers %s", f"enabled ({llm.model}, ${llm.monthly_budget_usd:.2f}/month)" if llm
             else "disabled (no ANTHROPIC_API_KEY)")

    client = SignalClient(os.environ.get("SIGNAL_API_URL", "http://signal-api:8080"), number)
    bot = GameBot(
        store,
        client,
        tz=os.environ.get("TIMEZONE", "America/New_York"),
        post_hour=int(os.environ.get("POST_HOUR", "18")),
        llm=llm,
    )
    asyncio.create_task(scheduler(bot, client))
    async for envelope in client.envelopes():
        try:
            await bot.handle_envelope(envelope)
        except Exception:
            log.exception("failed to handle message")


if __name__ == "__main__":
    asyncio.run(main())

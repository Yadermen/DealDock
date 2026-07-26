import asyncio

from aiogram import Bot

from steam_radar.config import get_settings


async def main() -> None:
    bot = Bot(get_settings().bot_token)
    try:
        info = await bot.get_me()
        print(f"username=@{info.username}")
        print(f"supports_inline_queries={bool(info.supports_inline_queries)}")
        if not info.supports_inline_queries:
            print("Enable it in @BotFather: /setinline -> select bot -> set an inline placeholder")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

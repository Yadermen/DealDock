from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from steam_radar.db.models import Game, User, WatchRule


async def get_or_create_user(
    session: AsyncSession, telegram_id: int, username: str | None, display_name: str | None = None
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, username=username, display_name=display_name)
        session.add(user)
        await session.flush()
    elif user.username != username:
        user.username = username
    if display_name and user.display_name != display_name:
        user.display_name = display_name
    user.last_seen_at = datetime.now(UTC)
    return user


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def watch_count(session: AsyncSession, user_id: int) -> int:
    return int(
        await session.scalar(select(func.count()).where(WatchRule.user_id == user_id, WatchRule.enabled.is_(True))) or 0
    )


async def get_or_create_game(session: AsyncSession, app_id: int, name: str, header_image: str | None = None) -> Game:
    game = await session.scalar(select(Game).where(Game.steam_app_id == app_id))
    if game is None:
        game = Game(steam_app_id=app_id, name=name, header_image=header_image)
        session.add(game)
        await session.flush()
    else:
        game.name, game.header_image = name, header_image
    return game

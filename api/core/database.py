# type: ignore

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.config import settings

# Create async engine.
#
# A pooled connection can die without the client noticing -- an Azure failover
# or maintenance event, or a network path that drops an idle socket. The server
# does not time these out itself (idle_in_transaction_session_timeout is 0), so
# nothing surfaces a dead connection until a statement fails on it, by which
# point it has already been handed to a request. pre_ping spends one cheap
# round-trip per checkout to reconnect transparently instead, and recycling caps
# how long any one connection is kept regardless.
ENGINE_OPTIONS = {
    "echo": False,
    "future": True,
    "pool_pre_ping": True,
    "pool_recycle": 1800,
}

task_engine = create_async_engine(settings.TASK_DATABASE_URL, **ENGINE_OPTIONS)
osm_engine = create_async_engine(settings.OSM_DATABASE_URL, **ENGINE_OPTIONS)

# Create async session factory
async_task_session = sessionmaker(
    class_=AsyncSession, expire_on_commit=False, bind=task_engine
)
async_osm_session = sessionmaker(
    class_=AsyncSession, expire_on_commit=False, bind=osm_engine
)

# Create declarative base for models
Base = declarative_base()


async def get_task_session() -> AsyncSession:
    async with async_task_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_osm_session() -> AsyncSession:
    async with async_osm_session() as session:
        try:
            yield session
        finally:
            await session.close()

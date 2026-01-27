# type: ignore

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from api.core.config import settings

# Create async engine
task_engine = create_async_engine(settings.TASK_DATABASE_URL, echo=False, future=True)
osm_engine = create_async_engine(settings.OSM_DATABASE_URL, echo=False, future=True)

# Create async session factory
async_task_session = sessionmaker(class_=AsyncSession, expire_on_commit=False, bind=task_engine)
async_osm_session = sessionmaker(class_=AsyncSession, expire_on_commit=False, bind=osm_engine)

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

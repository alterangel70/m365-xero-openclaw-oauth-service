import redis.asyncio as aioredis

from app.infrastructure.config import get_settings


async def create_redis_pool() -> aioredis.Redis:
    """
    Create and return an async Redis client backed by a connection pool.

    The caller is responsible for calling .aclose() on shutdown (handled via
    the FastAPI lifespan context in main.py).
    """
    settings = get_settings()
    return aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
    )

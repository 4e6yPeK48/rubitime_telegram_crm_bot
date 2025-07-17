import os
import time

from dotenv import load_dotenv
from sqlalchemy import select

from static.models import async_session, Service

load_dotenv()

_services_cache = {}
CACHE_EXPIRED_TIMEOUT = int(os.getenv("CACHE_EXPIRED_TIMEOUT"))


def _cache_expired(ts, timeout=CACHE_EXPIRED_TIMEOUT):
    return time.time() - ts > timeout


async def get_services(force_refresh=False):
    async with async_session() as session:
        result = await session.execute(select(Service))
        return result.scalars().all()


async def get_services_by_cooperator(cooperator_id: int, force_refresh=False):
    cache = _services_cache.get(cooperator_id)
    if not force_refresh and cache and not _cache_expired(cache["ts"]):
        return cache["value"]
    async with async_session() as session:
        result = await session.execute(select(Service).where(Service.cooperator_id == cooperator_id))
        services = result.scalars().all()
        _services_cache[cooperator_id] = {"value": services, "ts": time.time()}
        return services


def clear_services_cache(cooperator_id=None):
    global _services_cache
    if cooperator_id is None:
        _services_cache = {}
    else:
        _services_cache.pop(cooperator_id, None)


async def add_service(id: int, branch_id: int, cooperator_id: int, name: str, price: float, duration: int):
    async with async_session() as session:
        async with session.begin():
            exists = await session.get(Service, id)
            if exists:
                return False
            service = Service(
                id=id, branch_id=branch_id, cooperator_id=cooperator_id,
                name=name, price=price, duration=duration
            )
            session.add(service)
    clear_services_cache(cooperator_id)
    return True

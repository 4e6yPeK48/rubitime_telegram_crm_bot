import os
import time

from dotenv import load_dotenv
from sqlalchemy import select

from static.models import Cooperator, async_session

load_dotenv()

_cooperators_cache = {"value": None, "ts": 0}
CACHE_EXPIRED_TIMEOUT = int(os.getenv("CACHE_EXPIRED_TIMEOUT"))


def _cache_expired(ts, timeout=CACHE_EXPIRED_TIMEOUT):
    return time.time() - ts > timeout


async def get_cooperators(force_refresh=False):
    if not force_refresh and _cooperators_cache["value"] is not None and not _cache_expired(_cooperators_cache["ts"]):
        return _cooperators_cache["value"]
    async with async_session() as session:
        result = await session.execute(select(Cooperator))
        cooperators = result.scalars().all()
        _cooperators_cache["value"] = cooperators
        _cooperators_cache["ts"] = time.time()
        return cooperators


def clear_cooperators_cache():
    _cooperators_cache["value"] = None
    _cooperators_cache["ts"] = 0


async def add_cooperator(id: int, branch_id: int, name: str):
    async with async_session() as session:
        async with session.begin():
            exists = await session.get(Cooperator, id)
            if exists:
                return False
            cooperator = Cooperator(id=id, branch_id=branch_id, name=name)
            session.add(cooperator)
    clear_cooperators_cache()
    return True

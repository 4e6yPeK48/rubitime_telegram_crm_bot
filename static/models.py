from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Boolean

DATABASE_URL = "sqlite+aiosqlite:///rubitime.db"
engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()


class Cooperator(Base):
    """Модель сотрудника."""
    __tablename__ = "cooperators"
    id = Column(Integer, primary_key=True)
    branch_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    services = relationship("Service", back_populates="cooperator")


class Service(Base):
    """Модель услуги."""
    __tablename__ = "services"
    id = Column(Integer, primary_key=True)
    branch_id = Column(Integer, nullable=False)
    cooperator_id = Column(Integer, ForeignKey("cooperators.id"), nullable=False)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    duration = Column(Integer, nullable=False)
    cooperator = relationship("Cooperator", back_populates="services")


class ReminderRecord(Base):
    """Модель записи напоминания."""
    __tablename__ = "reminder_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rubitime_id = Column(Integer, nullable=True)
    user_id = Column(Integer, nullable=False)
    datetime = Column(DateTime, nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    reminded_24h = Column(Boolean, default=False)
    reminded_12h = Column(Boolean, default=False)
    confirmed = Column(Boolean, default=False)


async def init_db() -> None:
    """Инициализирует базу данных."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

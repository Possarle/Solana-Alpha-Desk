from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import JSON, Float, Integer, Numeric, String, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .schemas import now


class Base(DeclarativeBase):
    pass


class Config(Base):
    __tablename__ = "config"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class Token(Base):
    __tablename__ = "tokens"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    updated: Mapped[float] = mapped_column(Float)


class Pool(Base):
    __tablename__ = "pools"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    token_key: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_key: Mapped[str] = mapped_column(String, index=True)
    at: Mapped[float] = mapped_column(Float, default=now)
    data: Mapped[dict] = mapped_column(JSON)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_key: Mapped[str] = mapped_column(String, index=True)
    at: Mapped[float] = mapped_column(Float, default=now)
    data: Mapped[dict] = mapped_column(JSON)
    security: Mapped[dict] = mapped_column(JSON)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[float] = mapped_column(Float, default=now)
    data: Mapped[dict] = mapped_column(JSON)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[float] = mapped_column(Float, default=now)
    kind: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String, default="info")
    message: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Bench(Base):
    __tablename__ = "blocks"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    until: Mapped[float] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSON)


class Wallet(Base):
    __tablename__ = "wallets"
    address: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class WalletEvent(Base):
    __tablename__ = "wallet_events"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    wallet: Mapped[str] = mapped_column(String, index=True)
    at: Mapped[float] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSON)


class Evidence(Base):
    __tablename__ = "social_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_key: Mapped[str] = mapped_column(String, index=True)
    at: Mapped[float] = mapped_column(Float, default=now)
    data: Mapped[dict] = mapped_column(JSON)


class Account(Base):
    __tablename__ = "account"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    initial: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    cash: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    day: Mapped[str] = mapped_column(String)
    day_start: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    halted: Mapped[bool] = mapped_column(default=False)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_key: Mapped[str] = mapped_column(String, index=True)
    active_slot: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)
    qty: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    cost: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    entry: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    mark: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    peak: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    opened_at: Mapped[float] = mapped_column(Float)
    closed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    data: Mapped[dict] = mapped_column(JSON)


class Order(Base):
    __tablename__ = "orders"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    at: Mapped[float] = mapped_column(Float, default=now)
    data: Mapped[dict] = mapped_column(JSON)


class Equity(Base):
    __tablename__ = "equity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[float] = mapped_column(Float, default=now)
    value: Mapped[Decimal] = mapped_column(Numeric(28, 12))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Database:
    def __init__(self, url: str):
        kwargs = (
            {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
        )
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def sqlite_setup(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        with self.sessions.begin() as session:
            yield session

    def config(self, key: str, default: dict) -> dict:
        with self.session() as s:
            obj = s.get(Config, key)
            return obj.data if obj else default

    def set_config(self, key: str, data: dict):
        with self.session() as s:
            s.merge(Config(key=key, data=data))

    def event(self, kind: str, message: str, level: str = "info", data: dict | None = None):
        with self.session() as s:
            s.add(Event(kind=kind, message=message, level=level, data=data or {}))

    def rows(self, cls, limit: int = 100):
        with self.session() as s:
            return s.scalars(select(cls).order_by(cls.at.desc()).limit(limit)).all()


class LiveBook(Base):
    __tablename__ = "live_book"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class LiveOrder(Base):
    __tablename__ = "live_orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    active_slot: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String)
    at: Mapped[float] = mapped_column(Float, default=now)
    data: Mapped[dict] = mapped_column(JSON, default=dict)

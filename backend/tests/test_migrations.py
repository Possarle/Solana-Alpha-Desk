from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import settings


def test_migrations_upgrade_downgrade_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "migration.db"
    monkeypatch.setattr(settings, "database_url", "sqlite:///" + str(path))
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "head")
    engine = create_engine(settings.database_url)
    assert {"positions", "orders", "tokens", "wallet_events", "social_evidence"} <= set(
        inspect(engine).get_table_names()
    )
    command.downgrade(cfg, "base")
    assert "positions" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    assert "positions" in inspect(engine).get_table_names()
    engine.dispose()

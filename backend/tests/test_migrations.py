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


def test_supervised_migration_preserves_existing_data(tmp_path, monkeypatch):
    from sqlalchemy import text

    monkeypatch.setattr(settings, "database_url", "sqlite:///" + str(tmp_path / "upgrade.db"))
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "0001")
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO config (key,data) VALUES (:key, :data)"),
            {"key": "preserved", "data": '{"value":17}'},
        )
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT data FROM config WHERE key='preserved'")).scalar()
            == '{"value":17}'
        )
    assert {"live_orders", "live_book", "cohort"} <= set(inspect(engine).get_table_names())
    engine.dispose()

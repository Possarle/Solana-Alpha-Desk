"""Terminal report: are delays costing money in the shadow trades? Read-only.

Usage (from the repository root):  .venv/bin/python scripts/timing_audit.py
"""

import os
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

root = Path(__file__).resolve().parents[1]
env = root / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
os.chdir(root / "backend")
sys.path.insert(0, str(root / "backend"))


def usd(v):
    return "—" if v is None else f"${v:,.2f}"


def num(v, suffix=""):
    return "—" if v is None else f"{v:,.1f}{suffix}"


def load():
    from app.db import Database
    from app.timing import timing_report

    return timing_report(
        Database(os.environ.get("DATABASE_URL", "sqlite:///./alpha.db"))
    )


try:
    r = load()
except SQLAlchemyError as exc:  # e.g. database not migrated yet
    print("\nNão foi possível ler o banco do robô:", type(exc).__name__)
    print("Inicie o robô ao menos uma vez com:  bash scripts/start.sh\n")
    sys.exit(1)
print("\n=== AUDITORIA DE TEMPO — operações SHADOW encerradas ===\n")
print(f"Operações analisadas ........ {r['trades']}")
print(f"Resultado estimado .......... {usd(r['pnl'])}")
print(f"Taxas + slippage estimados .. {usd(r['fees_and_slippage'])}")
print(
    f"Custo do atraso na COMPRA ... {usd(r['entry_latency_cost'])}  (positivo = preço já tinha subido)"
)
print(
    f"Custo do atraso no STOP ..... {usd(r['exit_gap_cost'])}  (positivo = vendeu abaixo do stop)"
)
print(
    f"Idade do dado na compra ..... {num(r['median_entry_data_age_s'], ' s')} (mediana)"
)
print(
    f"Preço logo após a compra .... {num(r['median_entry_drift_pct'], '%')} (mediana)"
)
print(f"Passou do stop em ........... {num(r['median_exit_gap_pct'], '%')} (mediana)")
print(f"Gatilho → venda ............. {num(r['median_exit_lag_s'], ' s')} (mediana)")
print(f"Tempo em posição ............ {num(r['median_hold_min'], ' min')} (mediana)")
print("\nResultado por motivo de saída:")
for row in r["by_reason"]:
    print(
        f"  {row['reason']:<22} {row['trades']:>3} operações  {usd(row['pnl']):>12}  {row['wins']} positivas"
    )
print("\nConclusão:", r["verdict"])
print("\nMétodo:", r["method"], "\n")

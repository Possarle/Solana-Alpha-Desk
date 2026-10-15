"""Terminal report: why has the robot not bought? Read-only.

Usage (from the repository root):  .venv/bin/python scripts/why_no_trade.py [hours]
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


def load(hours):
    from app.db import Database
    from app.funnel import funnel_report

    return funnel_report(
        Database(os.environ.get("DATABASE_URL", "sqlite:///./alpha.db")), hours
    )


hours = int(sys.argv[1]) if len(sys.argv) > 1 else 48
try:
    r = load(hours)
except SQLAlchemyError as exc:  # e.g. database not migrated yet
    print("\nNão foi possível ler o banco do robô:", type(exc).__name__)
    print("Inicie o robô ao menos uma vez com:  bash scripts/start.sh\n")
    sys.exit(1)
print(f"\n=== POR QUE O ROBÔ NÃO COMPROU? — últimas {hours} h ===\n")
up = (
    f"{r['running_minutes']} min"
    if r["running_minutes"] < 60
    else f"{r['running_hours']} h"
)
print(f"Robô ligado ................. {up} de {hours} h  ({r['cycles']} ciclos)")
print(
    f"Vezes que ficou desligado ... {r['offline_periods']}  (maior: {r['longest_offline_hours']} h)"
)
if r["offline_now_minutes"] is not None:
    print(f"Último ciclo ................ há {r['offline_now_minutes']} min")
print(f"Tokens avaliados ............ {r['tokens_evaluated']}")
print(f"Tokens aprovados ............ {r['tokens_approved']}")
print(f"Compras simuladas ........... {r['entries']}")
print("\nRegras que mais bloquearam (tokens):")
for row in r["blockers"]:
    print(f"  {row['tokens']:>5}  {row['reason']}")
print("\nÚnico motivo do bloqueio (quase aprovados):")
for row in r["only_blocker"] or [{"tokens": 0, "reason": "nenhum"}]:
    print(f"  {row['tokens']:>5}  {row['reason']}")
if r["entry_blocks"]:
    print("\nAprovados que não viraram compra:")
    for row in r["entry_blocks"]:
        print(f"  {row['times']:>5}  {row['reason']}")
print("\n" + r["method"] + "\n")

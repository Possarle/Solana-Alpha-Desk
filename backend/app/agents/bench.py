from ..db import Bench, Database
from ..schemas import now


class Benches:
    def __init__(self, db: Database):
        self.db = db

    def blocked(self, key: str) -> bool:
        with self.db.session() as s:
            item = s.get(Bench, key)
            return bool(item and item.until > now())

    def reject(self, key: str, reasons: list[str], seconds: int):
        with self.db.session() as s:
            s.merge(Bench(key=key, until=now() + seconds, data={"reasons": reasons, "duration": seconds}))

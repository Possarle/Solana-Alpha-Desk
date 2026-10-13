from sqlalchemy import select

from ..db import Evidence


class SocialAgent:
    def __init__(self, db):
        self.db = db

    def analyze(self, key: str) -> dict:
        with self.db.session() as s:
            rows = s.scalars(
                select(Evidence).where(Evidence.token_key == key).order_by(Evidence.at.desc())
            ).all()
        return {
            "score": None,
            "evidence": [x.data | {"at": x.at} for x in rows],
            "unknown": [
                "Idade da conta",
                "Engajamento autêntico",
                "Conta reciclada",
                "Vínculo oficial não auditado",
            ],
            "method": "Evidência manual com URL; existência de link não comprova autenticidade. Sem scraping do X.",
        }

"""Observed agent activity only. No synthetic progress or model conversations."""

from collections import deque
from uuid import uuid4

from pydantic import BaseModel, Field

from .schemas import now


class AgentActivity(BaseModel):
    name: str
    state: str = "waiting"
    message: str = "Aguardando execução"
    at: float | None = None
    token_key: str | None = None
    data: dict = Field(default_factory=dict)


class DeskTelemetry:
    names = ("crawler", "scan", "vet", "social", "smart", "judge", "chief", "size", "fills", "risk")

    def __init__(self):
        self.agents = {name: AgentActivity(name=name) for name in self.names}
        self.events = deque(maxlen=80)
        self.cycle = None
        self.sequence = 0
        self.session = uuid4().hex

    def emit(self, name: str, state: str, message: str, token_key=None, **data):
        activity = AgentActivity(
            name=name, state=state, message=message, at=now(), token_key=token_key, data=data
        )
        self.agents[name] = activity
        self.sequence += 1
        self.events.appendleft({"id": f"{self.session}:{self.sequence}", **activity.model_dump()})

    def start_cycle(self):
        self.cycle = {
            "id": uuid4().hex,
            "started_at": now(),
            "finished_at": None,
            "received": 0,
            "evaluated": 0,
            "approved": 0,
            "rejected": 0,
            "blocked": 0,
        }

    def snapshot(self):
        return {
            "agents": [a.model_dump() for a in self.agents.values()],
            "events": list(self.events),
            "cycle": self.cycle,
        }

from typing import Protocol

from app.schemas import TournamentPayload


class TournamentProvider(Protocol):
    def load(self) -> TournamentPayload: ...


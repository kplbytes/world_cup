from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import ProviderQuotaState

class QuotaGuard:
    def __init__(self, session: Session, provider: str, daily_limit: int):
        self.session = session
        self.provider = provider
        self.daily_limit = daily_limit

    def can_request(self, cost: int = 1) -> bool:
        state = self._get_or_create_state()
        if state.used_today + cost > self.daily_limit:
            return False
        return True

    def record_request(self, cost: int = 1) -> None:
        state = self._get_or_create_state()
        state.used_today += cost
        state.updated_at = datetime.now(timezone.utc)
        self.session.flush()

    def remaining(self) -> int:
        state = self._get_or_create_state()
        return max(0, self.daily_limit - state.used_today)

    def _get_or_create_state(self) -> ProviderQuotaState:
        now = datetime.now(timezone.utc)
        state = self.session.get(ProviderQuotaState, self.provider)
        if state is None:
            # First time
            state = ProviderQuotaState(
                provider=self.provider,
                reset_at=self._next_reset(now),
                daily_limit=self.daily_limit,
                used_today=0,
            )
            self.session.add(state)
        elif state.reset_at and now >= state.reset_at.replace(tzinfo=timezone.utc):
            # Reset quota
            state.reset_at = self._next_reset(now)
            state.used_today = 0
            state.daily_limit = self.daily_limit
        else:
            # Update limit if configured limit changed
            state.daily_limit = self.daily_limit

        return state

    def _next_reset(self, now: datetime) -> datetime:
        # Reset at UTC midnight
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)

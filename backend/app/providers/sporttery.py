import json
from dataclasses import dataclass
from math import isfinite


class SportteryUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketProbability:
    home: float
    draw: float
    away: float
    raw_overround: float


def normalize_had_prices(home: float, draw: float, away: float) -> MarketProbability:
    prices = (home, draw, away)
    if any(not isfinite(price) or price <= 1.0 for price in prices):
        raise ValueError("decimal HAD prices must be finite and greater than one")
    implied = tuple(1.0 / price for price in prices)
    overround = sum(implied)
    return MarketProbability(
        home=implied[0] / overround,
        draw=implied[1] / overround,
        away=implied[2] / overround,
        raw_overround=overround,
    )


def parse_response(body: str) -> dict:
    stripped = body.lstrip()
    if stripped.startswith("<"):
        if "WAF" in body or "拦截" in body:
            raise SportteryUnavailable("Sporttery WAF blocked the request")
        raise SportteryUnavailable("Sporttery returned HTML instead of JSON")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SportteryUnavailable("Sporttery returned invalid JSON") from exc
    if payload.get("errorCode") != 0 or not isinstance(payload.get("value"), dict):
        raise SportteryUnavailable("Sporttery response is unavailable")
    return payload


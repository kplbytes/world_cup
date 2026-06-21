"""统一 API 客户端模块

提供多个免费足球数据源的统一访问接口，支持速率限制、本地缓存和错误处理。

数据源:
  - football-data.org: 免费 10 req/min，含世界杯
  - API-Football / api-sports.io: 免费 100 req/day
  - Open-Meteo: 免费历史天气，无需 API key
  - StatsBomb Open Data: GitHub 开放数据
  - openfootball: GitHub 开放数据
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 缓存根目录
CACHE_ROOT = Path(__file__).parent.parent.parent.parent / "data" / "external" / "api_cache"


def _ensure_cache_dir(source: str) -> Path:
    """确保缓存目录存在。"""
    cache_dir = CACHE_ROOT / source
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_key(*args) -> str:
    """生成缓存文件名。"""
    key_str = "_".join(str(a) for a in args)
    return hashlib.md5(key_str.encode()).hexdigest()[:16]


def _load_cache(source: str, key: str) -> Any | None:
    """从本地缓存加载。"""
    cache_dir = _ensure_cache_dir(source)
    cache_file = cache_dir / f"{key}.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_cache(source: str, key: str, data: Any) -> None:
    """保存到本地缓存。"""
    cache_dir = _ensure_cache_dir(source)
    cache_file = cache_dir / f"{key}.json"
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except OSError as e:
        logger.warning(f"缓存写入失败: {e}")


class RateLimiter:
    """简单的速率限制器。"""

    def __init__(self, min_interval: float = 6.0, daily_limit: int | None = None):
        """
        Args:
            min_interval: 两次请求之间的最小间隔（秒）
            daily_limit: 每日请求上限（None 表示无限制）
        """
        self._min_interval = min_interval
        self._daily_limit = daily_limit
        self._last_request_time: float = 0.0
        self._daily_count: int = 0
        self._daily_reset: float = time.time()

    def wait(self) -> None:
        """等待直到可以发起下一次请求。"""
        now = time.time()

        # 每日限额检查
        if self._daily_limit is not None:
            if now - self._daily_reset > 86400:
                self._daily_count = 0
                self._daily_reset = now
            if self._daily_count >= self._daily_limit:
                logger.warning("已达到每日请求上限，跳过请求")
                raise RuntimeError("每日请求上限已达")
            self._daily_count += 1

        # 最小间隔检查
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            sleep_time = self._min_interval - elapsed
            logger.debug(f"速率限制: 等待 {sleep_time:.1f}s")
            time.sleep(sleep_time)

        self._last_request_time = time.time()


class FootballDataOrgClient:
    """football-data.org API 客户端

    免费层: 10 req/min，含世界杯等 12 项赛事
    API: https://api.football-data.org/v4/
    认证: X-Auth-Token header
    """

    BASE_URL = "https://api.football-data.org/v4"
    # 世界杯 competition code
    WC_COMPETITION = "WC"

    def __init__(self, api_token: str | None = None):
        self._token = api_token or os.environ.get("FOOTBALL_DATA_ORG_TOKEN", "")
        self._rate_limiter = RateLimiter(min_interval=6.0)
        self._session = requests.Session()
        if self._token:
            self._session.headers["X-Auth-Token"] = self._token

    @property
    def is_available(self) -> bool:
        """是否有有效的 API token。"""
        return bool(self._token)

    def _request(self, endpoint: str, params: dict | None = None) -> dict | list | None:
        """发起 API 请求，带缓存和错误处理。"""
        cache_key_str = f"{endpoint}_{json.dumps(params or {}, sort_keys=True)}"
        key = _cache_key("fd", cache_key_str)

        # 尝试缓存
        cached = _load_cache("football_data_org", key)
        if cached is not None:
            logger.debug(f"缓存命中: {endpoint}")
            return cached

        if not self.is_available:
            logger.warning("football-data.org: 无 API token，跳过请求")
            return None

        try:
            self._rate_limiter.wait()
            url = f"{self.BASE_URL}{endpoint}"
            logger.info(f"API 请求: {url}")
            resp = self._session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            _save_cache("football_data_org", key, data)
            return data
        except requests.exceptions.RequestException as e:
            logger.warning(f"football-data.org 请求失败: {e}")
            return None
        except RuntimeError as e:
            # 速率限制
            logger.warning(f"football-data.org 速率限制: {e}")
            return None

    def get_world_cup_matches(self, season_year: int = 2026) -> list[dict]:
        """获取世界杯比赛列表。

        Args:
            season_year: 赛季年份

        Returns:
            比赛字典列表
        """
        data = self._request(
            f"/competitions/{self.WC_COMPETITION}/matches",
            params={"season": season_year},
        )
        if data is None:
            return []
        matches = data.get("matches", [])
        logger.info(f"获取到 {len(matches)} 场世界杯比赛 (season={season_year})")
        return matches

    def get_team_matches(self, team_id: int) -> list[dict]:
        """获取某球队的比赛列表。

        Args:
            team_id: 球队 ID

        Returns:
            比赛字典列表
        """
        data = self._request(f"/teams/{team_id}/matches")
        if data is None:
            return []
        return data.get("matches", [])

    def get_head2head(self, team1_id: int, team2_id: int) -> dict:
        """获取两队交锋记录。

        Args:
            team1_id: 球队1 ID
            team2_id: 球队2 ID

        Returns:
            交锋记录字典
        """
        data = self._request(f"/matches/{team1_id}/head2head/{team2_id}")
        return data if data else {}

    def get_match(self, match_id: int) -> dict:
        """获取单场比赛详情。

        Args:
            match_id: 比赛 ID

        Returns:
            比赛详情字典
        """
        data = self._request(f"/matches/{match_id}")
        return data if data else {}

    def get_squad(self, team_id: int) -> list[dict]:
        """获取球队阵容。

        Args:
            team_id: 球队 ID

        Returns:
            球员字典列表
        """
        data = self._request(f"/teams/{team_id}")
        if data is None:
            return []
        return data.get("squad", [])


class APIFootballClient:
    """API-Football 客户端

    免费层: 100 req/day
    API: https://v3.football.api-sports.io/
    认证: x-apisport-key header
    """

    BASE_URL = "https://v3.football.api-sports.io"

    def __init__(self, api_key: str | None = None):
        self._key = api_key or os.environ.get("API_FOOTBALL_KEY", "")
        self._rate_limiter = RateLimiter(min_interval=1.0, daily_limit=95)
        self._session = requests.Session()
        if self._key:
            self._session.headers["x-apisport-key"] = self._key

    @property
    def is_available(self) -> bool:
        """是否有有效的 API key。"""
        return bool(self._key)

    def _request(self, endpoint: str, params: dict | None = None) -> dict | None:
        """发起 API 请求，带缓存和错误处理。"""
        cache_key_str = f"{endpoint}_{json.dumps(params or {}, sort_keys=True)}"
        key = _cache_key("af", cache_key_str)

        cached = _load_cache("api_football", key)
        if cached is not None:
            logger.debug(f"缓存命中: {endpoint}")
            return cached

        if not self.is_available:
            logger.warning("API-Football: 无 API key，跳过请求")
            return None

        try:
            self._rate_limiter.wait()
            url = f"{self.BASE_URL}{endpoint}"
            logger.info(f"API 请求: {url}")
            resp = self._session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            _save_cache("api_football", key, data)
            return data
        except requests.exceptions.RequestException as e:
            logger.warning(f"API-Football 请求失败: {e}")
            return None
        except RuntimeError as e:
            logger.warning(f"API-Football 速率限制: {e}")
            return None

    def get_injuries(self, fixture_id: int) -> list[dict]:
        """获取比赛伤病信息。

        Args:
            fixture_id: 比赛 ID

        Returns:
            伤病信息列表
        """
        data = self._request("/injuries", params={"fixture": fixture_id})
        if data is None:
            return []
        return data.get("response", [])

    def get_predictions(self, fixture_id: int) -> dict:
        """获取比赛预测。

        Args:
            fixture_id: 比赛 ID

        Returns:
            预测字典
        """
        data = self._request("/predictions", params={"fixture": fixture_id})
        if data is None:
            return {}
        responses = data.get("response", [])
        return responses[0] if responses else {}

    def get_odds(self, fixture_id: int) -> list[dict]:
        """获取比赛赔率。

        Args:
            fixture_id: 比赛 ID

        Returns:
            赔率列表
        """
        data = self._request("/odds", params={"fixture": fixture_id})
        if data is None:
            return []
        return data.get("response", [])

    def get_fixture(self, fixture_id: int) -> dict:
        """获取比赛详情。

        Args:
            fixture_id: 比赛 ID

        Returns:
            比赛详情字典
        """
        data = self._request("/fixtures", params={"id": fixture_id})
        if data is None:
            return {}
        responses = data.get("response", [])
        return responses[0] if responses else {}

    def get_head2head(self, team1_id: int, team2_id: int) -> dict:
        """获取两队交锋记录。

        Args:
            team1_id: 球队1 ID
            team2_id: 球队2 ID

        Returns:
            交锋记录字典
        """
        data = self._request(
            "/fixtures/headtohead",
            params={"h2h": f"{team1_id}-{team2_id}"},
        )
        return data if data else {}

    def get_coach(self, team_id: int) -> dict:
        """获取球队教练信息。

        Args:
            team_id: 球队 ID

        Returns:
            教练信息字典
        """
        data = self._request("/coachs", params={"team": team_id})
        if data is None:
            return {}
        responses = data.get("response", [])
        return responses[0] if responses else {}


class OpenMeteoClient:
    """Open-Meteo 历史天气客户端

    免费，无需 API key，无速率限制
    API: https://archive-api.open-meteo.com/v1/archive
    """

    BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

    # 世界杯主办城市坐标映射
    WORLD_CUP_CITIES: dict[str, tuple[float, float]] = {
        # 2022 卡塔尔
        "Doha": (25.2854, 51.5310),
        "Al Khor": (25.6833, 51.5000),
        "Al Wakrah": (25.1731, 51.6025),
        "Lusail": (25.4167, 51.5000),
        "Al Rayyan": (25.2667, 51.4167),
        # 2018 俄罗斯
        "Moscow": (55.7558, 37.6173),
        "St. Petersburg": (59.9343, 30.3351),
        "Kazan": (55.7887, 49.1221),
        "Sochi": (43.6028, 39.7342),
        "Nizhny Novgorod": (56.3269, 44.0059),
        "Samara": (53.1959, 50.1002),
        "Volgograd": (48.7080, 44.5133),
        "Rostov-on-Don": (47.2357, 39.7015),
        "Yekaterinburg": (56.8389, 60.6057),
        "Saransk": (54.1838, 45.1749),
        "Kaliningrad": (54.7104, 20.4522),
        # 2014 巴西
        "Rio de Janeiro": (-22.9068, -43.1729),
        "São Paulo": (-23.5505, -46.6333),
        "Brasília": (-15.7975, -47.8919),
        "Salvador": (-12.9714, -38.5124),
        "Fortaleza": (-3.7172, -38.5433),
        "Belo Horizonte": (-19.9167, -43.9345),
        "Manaus": (-3.1190, -60.0217),
        "Curitiba": (-25.4284, -49.2733),
        "Recife": (-8.0476, -34.8770),
        "Porto Alegre": (-30.0346, -51.2177),
        "Natal": (-5.7945, -35.2110),
        "Cuiabá": (-15.6014, -56.0979),
        # 2010 南非
        "Johannesburg": (-26.2041, 28.0473),
        "Cape Town": (-33.9249, 18.4241),
        "Durban": (-29.8587, 31.0218),
        "Pretoria": (-25.7479, 28.2293),
        "Port Elizabeth": (-33.9608, 25.6022),
        "Bloemfontein": (-29.0852, 26.1596),
        "Rustenburg": (-25.6676, 27.2421),
        "Nelspruit": (-25.4753, 30.9694),
        "Polokwane": (-23.9045, 29.4688),
        # 2026 北美
        "New York": (40.7128, -74.0060),
        "Los Angeles": (34.0522, -118.2437),
        "Dallas": (32.7767, -96.7970),
        "Miami": (25.7617, -80.1918),
        "Atlanta": (33.7490, -84.3880),
        "Seattle": (47.6062, -122.3321),
        "San Francisco": (37.7749, -122.4194),
        "Houston": (29.7604, -95.3698),
        "Boston": (42.3601, -71.0589),
        "Philadelphia": (39.9526, -75.1652),
        "Kansas City": (39.0997, -94.5786),
        "Mexico City": (19.4326, -99.1332),
        "Guadalajara": (20.6597, -103.3496),
        "Monterrey": (25.6866, -100.3161),
        "Toronto": (43.6532, -79.3832),
        "Vancouver": (49.2827, -123.1207),
        "Edmonton": (53.5461, -113.4938),
    }

    def __init__(self):
        self._session = requests.Session()

    def get_weather(self, lat: float, lon: float, date: str) -> dict:
        """获取指定坐标和日期的历史天气数据。

        Args:
            lat: 纬度
            lon: 经度
            date: 日期字符串 (YYYY-MM-DD)

        Returns:
            天气数据字典，包含 temperature, humidity, wind_speed, precipitation, weather_code
        """
        cache_key_str = f"weather_{lat}_{lon}_{date}"
        key = _cache_key("om", cache_key_str)

        cached = _load_cache("open_meteo", key)
        if cached is not None:
            return cached

        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": date,
                "end_date": date,
                "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,weather_code",
                "timezone": "auto",
            }
            logger.info(f"Open-Meteo 请求: lat={lat}, lon={lon}, date={date}")
            resp = self._session.get(self.BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            raw = resp.json()

            # 提取日均值（取比赛时间附近 12:00-18:00 的平均）
            result = self._extract_match_weather(raw)
            _save_cache("open_meteo", key, result)
            return result
        except requests.exceptions.RequestException as e:
            logger.warning(f"Open-Meteo 请求失败: {e}")
            return {}
        except Exception as e:
            logger.warning(f"Open-Meteo 数据解析失败: {e}")
            return {}

    def get_weather_for_match(self, city: str, date: str) -> dict:
        """根据城市名获取比赛日天气。

        Args:
            city: 城市名（需在 WORLD_CUP_CITIES 中）
            date: 日期字符串 (YYYY-MM-DD)

        Returns:
            天气数据字典
        """
        coords = self.WORLD_CUP_CITIES.get(city)
        if coords is None:
            logger.warning(f"未知城市: {city}，跳过天气查询")
            return {}
        return self.get_weather(coords[0], coords[1], date)

    def _extract_match_weather(self, raw: dict) -> dict:
        """从 Open-Meteo 响应中提取比赛时段天气。"""
        try:
            hourly = raw.get("hourly", {})
            times = hourly.get("time", [])
            temps = hourly.get("temperature_2m", [])
            humidity = hourly.get("relative_humidity_2m", [])
            wind = hourly.get("wind_speed_10m", [])
            precip = hourly.get("precipitation", [])
            wcode = hourly.get("weather_code", [])

            if not times:
                return {}

            # 取 12:00-18:00 的数据（比赛时段）
            match_hours = []
            for i, t in enumerate(times):
                if isinstance(t, str) and ("T12" in t or "T13" in t or "T14" in t
                                           or "T15" in t or "T16" in t or "T17" in t
                                           or "T18" in t):
                    match_hours.append(i)

            if not match_hours:
                # 回退：取所有有效数据的均值
                match_hours = list(range(len(times)))

            import numpy as np

            def _safe_mean(vals, indices):
                valid = [vals[i] for i in indices if i < len(vals) and vals[i] is not None]
                return float(np.mean(valid)) if valid else None

            return {
                "temperature": _safe_mean(temps, match_hours),
                "humidity": _safe_mean(humidity, match_hours),
                "wind_speed": _safe_mean(wind, match_hours),
                "precipitation": _safe_mean(precip, match_hours),
                "weather_code": _safe_mean(wcode, match_hours),
            }
        except Exception as e:
            logger.warning(f"天气数据提取失败: {e}")
            return {}


class StatsBombLoader:
    """StatsBomb 开放数据加载器

    从 GitHub 加载世界杯赛事级数据（2010, 2014, 2018, 2022）
    URL: https://raw.githubusercontent.com/statsbomb/open-data/master/data/
    """

    BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

    # StatsBomb 世界杯 competition_id = 43
    WC_COMPETITION_ID = 43

    # 已知的世界杯 season_id 映射
    WC_SEASONS: dict[int, int] = {
        2010: 3,
        2014: 4,
        2018: 3,
        2022: 4,
    }

    def __init__(self):
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "WorldCupResearch/1.0"

    def _fetch_json(self, path: str) -> Any:
        """从 GitHub 获取 JSON 数据，带缓存。"""
        key = _cache_key("sb", path)
        cached = _load_cache("statsbomb", key)
        if cached is not None:
            return cached

        try:
            url = f"{self.BASE_URL}/{path}"
            logger.info(f"StatsBomb 请求: {url}")
            resp = self._session.get(url, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            _save_cache("statsbomb", key, data)
            return data
        except requests.exceptions.RequestException as e:
            logger.warning(f"StatsBomb 请求失败: {e}")
            return None

    def list_competitions(self) -> list[dict]:
        """列出所有可用赛事。"""
        data = self._fetch_json("competitions.json")
        return data if isinstance(data, list) else []

    def get_matches(self, competition_id: int, season_id: int) -> list[dict]:
        """获取某赛季比赛列表。

        Args:
            competition_id: 赛事 ID（世界杯=43）
            season_id: 赛季 ID

        Returns:
            比赛列表
        """
        data = self._fetch_json(f"matches/{competition_id}/{season_id}.json")
        return data if isinstance(data, list) else []

    def get_events(self, match_id: int) -> list[dict]:
        """获取比赛事件数据（含 xG）。

        Args:
            match_id: 比赛 ID

        Returns:
            事件列表
        """
        data = self._fetch_json(f"events/{match_id}.json")
        return data if isinstance(data, list) else []

    def get_lineups(self, match_id: int) -> list[dict]:
        """获取比赛阵容。

        Args:
            match_id: 比赛 ID

        Returns:
            阵容列表
        """
        data = self._fetch_json(f"lineups/{match_id}.json")
        return data if isinstance(data, list) else []

    def get_world_cup_matches(self, year: int) -> list[dict]:
        """获取指定年份世界杯比赛。

        Args:
            year: 世界杯年份 (2010, 2014, 2018, 2022)

        Returns:
            比赛列表
        """
        season_id = self.WC_SEASONS.get(year)
        if season_id is None:
            logger.warning(f"StatsBomb 无 {year} 世界杯数据")
            return []
        return self.get_matches(self.WC_COMPETITION_ID, season_id)

    def extract_xg_from_events(self, events: list[dict]) -> dict[str, list[float]]:
        """从事件数据中提取 xG 信息。

        Args:
            events: 事件列表

        Returns:
            {"home_xg": [...], "away_xg": [...]}
        """
        home_xg = []
        away_xg = []

        for event in events:
            if event.get("type", {}).get("name") != "Shot":
                continue
            shot = event.get("shot", {})
            xg = shot.get("statsbomb_xg")
            if xg is None:
                continue

            team = event.get("team", {}).get("name", "")
            # 需要外部提供主客队名称映射，此处简单收集
            home_xg.append(float(xg)) if not away_xg else away_xg.append(float(xg))

        return {"home_xg": home_xg, "away_xg": away_xg}


class OpenFootballLoader:
    """openfootball 数据加载器

    从 GitHub 加载世界杯比赛数据
    URL: https://raw.githubusercontent.com/openfootball/football.json/master/
    """

    BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"

    def __init__(self):
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "WorldCupResearch/1.0"

    def _fetch_json(self, path: str) -> Any:
        """从 GitHub 获取 JSON 数据，带缓存。"""
        key = _cache_key("of", path)
        cached = _load_cache("openfootball", key)
        if cached is not None:
            return cached

        try:
            url = f"{self.BASE_URL}/{path}"
            logger.info(f"openfootball 请求: {url}")
            resp = self._session.get(url, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            _save_cache("openfootball", key, data)
            return data
        except requests.exceptions.RequestException as e:
            logger.warning(f"openfootball 请求失败: {e}")
            return None

    def get_world_cup(self, year: int) -> dict:
        """获取指定年份世界杯数据。

        Args:
            year: 世界杯年份

        Returns:
            世界杯数据字典
        """
        data = self._fetch_json(f"2022/world-cup.json" if year == 2022
                                else f"2014/world-cup.json" if year == 2014
                                else f"2018/world-cup.json" if year == 2018
                                else f"2010/world-cup.json")
        return data if isinstance(data, dict) else {}

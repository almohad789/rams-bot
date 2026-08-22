"""Client pour l'API publique ESPN (non officielle, sans clé, à requêter avec parcimonie).

Endpoints utilisés :
  * calendrier d'une équipe : /teams/{id}/schedule?season=YYYY&seasontype=N
  * fiche complète d'un match : /summary?event={id}

Tout le parsing est défensif : ESPN change ses champs sans prévenir, on ne veut
pas qu'un bot plante parce qu'une clé a disparu.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import aiohttp

log = logging.getLogger("espn")

SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

SEASON_TYPES = {1: "Présaison", 2: "Saison régulière", 3: "Playoffs"}


def _dig(obj: Any, *path, default=None):
    """Descend dans des dicts/listes imbriqués sans lever d'exception."""
    cur = obj
    for key in path:
        if cur is None:
            return default
        try:
            if isinstance(key, int):
                cur = cur[key]
            else:
                cur = cur.get(key)
        except (KeyError, IndexError, TypeError, AttributeError):
            return default
    return cur if cur is not None else default


def _to_float(value) -> float | None:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass
class Team:
    id: str = ""
    abbr: str = ""
    name: str = ""
    logo: str = ""
    color: str = ""
    score: int | None = None
    winner: bool | None = None
    record: str = ""


@dataclass
class Game:
    id: str
    kickoff: datetime | None
    season: int
    season_type: int
    week: int | None
    home: Team
    away: Team
    state: str = "pre"           # pre / in / post
    completed: bool = False
    detail: str = ""             # "Final", "3e QT 07:42", ...
    venue: str = ""
    venue_city: str = ""
    venue_country: str = ""
    neutral_site: bool = False
    us_broadcasts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def side(self, team_id: str) -> Team:
        return self.home if self.home.id == team_id else self.away

    def other(self, team_id: str) -> Team:
        return self.away if self.home.id == team_id else self.home

    def is_home(self, team_id: str) -> bool:
        return self.home.id == team_id

    @property
    def season_label(self) -> str:
        base = SEASON_TYPES.get(self.season_type, "")
        if self.season_type == 2 and self.week:
            return f"{base}, semaine {self.week}"
        if self.season_type == 3 and self.notes:
            return self.notes[0]
        return base


def _parse_team(competitor: dict) -> Team:
    team = competitor.get("team") or {}
    score = competitor.get("score")
    if isinstance(score, dict):
        score = score.get("value", score.get("displayValue"))
    try:
        score = int(float(score)) if score not in (None, "") else None
    except (TypeError, ValueError):
        score = None

    record = ""
    records = competitor.get("records") or competitor.get("record") or []
    if isinstance(records, dict):
        records = records.get("items", [])
    for item in records or []:
        if isinstance(item, dict) and item.get("type") in (None, "total", "overall"):
            record = item.get("summary") or item.get("displayValue") or ""
            if record:
                break

    logos = team.get("logos") or []
    logo = team.get("logo") or (logos[0].get("href") if logos else "")

    return Team(
        id=str(team.get("id", "")),
        abbr=team.get("abbreviation") or team.get("abbrev") or "",
        name=team.get("displayName") or team.get("name") or "",
        logo=logo or "",
        color=team.get("color") or "",
        score=score,
        winner=competitor.get("winner"),
        record=record,
    )


def parse_event(event: dict) -> Game | None:
    comp = _dig(event, "competitions", 0, default={})
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None

    home = away = None
    for c in competitors:
        if c.get("homeAway") == "home":
            home = _parse_team(c)
        elif c.get("homeAway") == "away":
            away = _parse_team(c)
    if home is None or away is None:
        home, away = _parse_team(competitors[0]), _parse_team(competitors[1])

    broadcasts: list[str] = []
    for b in comp.get("broadcasts") or event.get("broadcasts") or []:
        for candidate in (
            _dig(b, "media", "shortName"),
            b.get("shortName"),
            b.get("name"),
        ):
            if candidate:
                broadcasts.append(str(candidate))
                break
        else:
            broadcasts.extend(str(n) for n in (b.get("names") or []))
    # dédoublonnage en conservant l'ordre
    broadcasts = list(dict.fromkeys(x for x in broadcasts if x))

    notes = [
        n.get("headline", "")
        for n in (comp.get("notes") or event.get("notes") or [])
        if isinstance(n, dict) and n.get("headline")
    ]

    return Game(
        id=str(event.get("id", "")),
        kickoff=_parse_date(comp.get("date") or event.get("date")),
        season=int(_dig(event, "season", "year", default=0) or 0),
        season_type=int(
            _dig(event, "seasonType", "id", default=None)
            or _dig(event, "season", "type", default=2)
            or 2
        ),
        week=_dig(event, "week", "number"),
        home=home,
        away=away,
        state=_dig(comp, "status", "type", "state", default="pre"),
        completed=bool(_dig(comp, "status", "type", "completed", default=False)),
        detail=_dig(comp, "status", "type", "detail", default="") or "",
        venue=_dig(comp, "venue", "fullName", default="") or "",
        venue_city=_dig(comp, "venue", "address", "city", default="") or "",
        venue_country=_dig(comp, "venue", "address", "country", default="") or "",
        neutral_site=bool(comp.get("neutralSite", False)),
        us_broadcasts=broadcasts,
        notes=notes,
    )


@dataclass
class HeadToHead:
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: int = 0
    points_against: int = 0
    last_games: list[str] = field(default_factory=list)   # "V 27:20 (2025)"
    seasons_covered: int = 0

    @property
    def played(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def summary(self) -> str:
        if not self.played:
            return "aucune confrontation sur la période"
        base = f"{self.wins}V {self.losses}D"
        if self.ties:
            base += f" {self.ties}N"
        return base

    @property
    def win_rate(self) -> float | None:
        if not self.played:
            return None
        return (self.wins + 0.5 * self.ties) / self.played


class EspnClient:
    """Petit client avec cache mémoire : ESPN n'aime pas être martelé."""

    def __init__(self, session: aiohttp.ClientSession, default_ttl: int = 300):
        self._session = session
        self._default_ttl = default_ttl
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _get(self, url: str, params: dict | None = None, ttl: int | None = None) -> dict:
        ttl = self._default_ttl if ttl is None else ttl
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))

        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < ttl:
            return cached[1]

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached and time.time() - cached[0] < ttl:
                return cached[1]

            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    async with self._session.get(
                        url, params=params, timeout=aiohttp.ClientTimeout(total=20)
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json(content_type=None)
                    self._cache[key] = (time.time(), data)
                    return data
                except Exception as exc:  # réseau, 5xx, JSON invalide
                    last_error = exc
                    await asyncio.sleep(2 ** attempt)

            log.warning("échec ESPN sur %s : %s", key, last_error)
            if cached:
                log.warning("réutilisation du cache périmé")
                return cached[1]
            raise last_error  # type: ignore[misc]

    async def team_schedule(self, team_id: str, season: int, season_type: int = 2) -> list[Game]:
        data = await self._get(
            f"{SITE}/teams/{team_id}/schedule",
            {"season": season, "seasontype": season_type},
            ttl=1800,
        )
        games = []
        for event in data.get("events") or []:
            game = parse_event(event)
            if game:
                if not game.season:
                    game.season = season
                if not game.season_type:
                    game.season_type = season_type
                games.append(game)
        return games

    async def full_season(self, team_id: str, season: int) -> list[Game]:
        """Présaison + saison régulière + playoffs, triés par date."""
        results: list[Game] = []
        for season_type in (1, 2, 3):
            try:
                results.extend(await self.team_schedule(team_id, season, season_type))
            except Exception as exc:
                log.warning("calendrier %s type %s indisponible : %s", season, season_type, exc)
        seen: dict[str, Game] = {}
        for game in results:
            seen.setdefault(game.id, game)
        return sorted(
            seen.values(), key=lambda g: g.kickoff or datetime.max.replace(tzinfo=timezone.utc)
        )

    async def summary(self, event_id: str, ttl: int = 120) -> dict:
        return await self._get(f"{SITE}/summary", {"event": event_id}, ttl=ttl)

    async def head_to_head(
        self, team_id: str, opponent_id: str, current_season: int, seasons_back: int = 6
    ) -> HeadToHead:
        """Bilan face à un adversaire sur les N dernières saisons (régulière + playoffs)."""
        h2h = HeadToHead(seasons_covered=seasons_back)
        rows: list[tuple[datetime, str]] = []

        for season in range(current_season, current_season - seasons_back, -1):
            for season_type in (2, 3):
                try:
                    games = await self.team_schedule(team_id, season, season_type)
                except Exception:
                    continue
                for game in games:
                    if not game.completed:
                        continue
                    opponent = game.other(team_id)
                    if opponent.id != opponent_id:
                        continue
                    us, them = game.side(team_id), opponent
                    if us.score is None or them.score is None:
                        continue
                    h2h.points_for += us.score
                    h2h.points_against += them.score
                    if us.score > them.score:
                        h2h.wins += 1
                        mark = "V"
                    elif us.score < them.score:
                        h2h.losses += 1
                        mark = "D"
                    else:
                        h2h.ties += 1
                        mark = "N"
                    lieu = "dom." if game.is_home(team_id) else "ext."
                    rows.append(
                        (
                            game.kickoff or datetime.min.replace(tzinfo=timezone.utc),
                            f"{mark} {us.score}:{them.score} ({lieu}, {season})",
                        )
                    )

        rows.sort(key=lambda r: r[0], reverse=True)
        h2h.last_games = [label for _, label in rows[:5]]
        return h2h


# ---------------------------------------------------------------------------
# Probabilité de victoire
# ---------------------------------------------------------------------------


def _implied_from_moneyline(moneyline) -> float | None:
    try:
        ml = float(moneyline)
    except (TypeError, ValueError):
        return None
    if ml < 0:
        return -ml / (-ml + 100.0)
    return 100.0 / (ml + 100.0)


@dataclass
class Prediction:
    probability: float | None = None      # chance de victoire de NOTRE équipe, 0..1
    source: str = ""
    spread: str = ""
    over_under: str = ""
    provider: str = ""

    @property
    def percent(self) -> str:
        if self.probability is None:
            return "non disponible"
        return f"{self.probability * 100:.0f} %"

    @property
    def verdict(self) -> str:
        p = self.probability
        if p is None:
            return "Pas assez de données pour trancher."
        if p >= 0.70:
            return "Large favori. La défaite serait une surprise."
        if p >= 0.58:
            return "Favori assez net, sans être à l'abri."
        if p >= 0.45:
            return "Match serré, ça se joue à peu de choses."
        if p >= 0.32:
            return "Outsider. Il faudra un très bon match."
        return "Nettement outsider sur le papier."


def prediction_from_summary(summary: dict, team_id: str, home_id: str) -> Prediction:
    """Utilise d'abord le Matchup Predictor ESPN, sinon les cotes moneyline."""
    pred = Prediction()

    predictor = summary.get("predictor") or {}
    home_proj = _to_float(_dig(predictor, "homeTeam", "gameProjection"))
    away_proj = _to_float(_dig(predictor, "awayTeam", "gameProjection"))
    if home_proj is not None or away_proj is not None:
        if home_proj is None and away_proj is not None:
            home_proj = 100.0 - away_proj
        if home_proj is not None:
            ours = home_proj if team_id == home_id else 100.0 - home_proj
            pred.probability = max(0.0, min(1.0, ours / 100.0))
            pred.source = "ESPN Matchup Predictor"

    books = summary.get("pickcenter") or []
    book = next((b for b in books if _dig(b, "provider", "name")), books[0] if books else None)
    if book:
        pred.provider = _dig(book, "provider", "name", default="") or ""
        pred.spread = book.get("details") or ""
        ou = book.get("overUnder")
        pred.over_under = str(ou) if ou is not None else ""
        if pred.probability is None:
            home_ml = _implied_from_moneyline(_dig(book, "homeTeamOdds", "moneyLine"))
            away_ml = _implied_from_moneyline(_dig(book, "awayTeamOdds", "moneyLine"))
            if home_ml and away_ml:
                total = home_ml + away_ml       # on retire la marge du bookmaker
                ours = home_ml if team_id == home_id else away_ml
                pred.probability = ours / total
                pred.source = f"cotes {pred.provider}".strip()

    return pred

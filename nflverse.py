"""Source de données : nflverse, hébergé dans les releases GitHub.

ESPN filtre les adresses IP des datacenters, ce qui rend son API inutilisable
depuis GitHub Actions. nflverse publie le calendrier NFL complet en CSV
directement sur GitHub, donc joignable depuis un runner Actions.

Contenu récupéré :
  * calendrier complet, horaires en heure de l'Est américain
  * cotes moneyline et spread, pour le pronostic
  * scores finaux, pour le rapport de fin de match
  * historique depuis 1999, pour le bilan face à un adversaire

Ce que nflverse ne fournit pas : la chaîne américaine. Elle est déduite du
créneau et de la conférence de l'équipe visiteuse, et signalée comme estimation.

Les dataclasses viennent de espn.py pour que presentation.py et diffusion.py
fonctionnent sans modification.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

from espn import Game, HeadToHead, Prediction, Team, _implied_from_moneyline

log = logging.getLogger("nflverse")

EST = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
CALENDRIER = f"{BASE}/schedules/games.csv"
STATS = BASE + "/stats_player/stats_player_week_{season}.csv"

LOGO = "https://a.espncdn.com/i/teamlogos/nfl/500/{code}.png"

# abréviation nflverse -> (nom complet, code logo, conférence)
EQUIPES = {
    "ARI": ("Arizona Cardinals", "ari", "NFC"), "ATL": ("Atlanta Falcons", "atl", "NFC"),
    "BAL": ("Baltimore Ravens", "bal", "AFC"), "BUF": ("Buffalo Bills", "buf", "AFC"),
    "CAR": ("Carolina Panthers", "car", "NFC"), "CHI": ("Chicago Bears", "chi", "NFC"),
    "CIN": ("Cincinnati Bengals", "cin", "AFC"), "CLE": ("Cleveland Browns", "cle", "AFC"),
    "DAL": ("Dallas Cowboys", "dal", "NFC"), "DEN": ("Denver Broncos", "den", "AFC"),
    "DET": ("Detroit Lions", "det", "NFC"), "GB": ("Green Bay Packers", "gb", "NFC"),
    "HOU": ("Houston Texans", "hou", "AFC"), "IND": ("Indianapolis Colts", "ind", "AFC"),
    "JAX": ("Jacksonville Jaguars", "jax", "AFC"), "KC": ("Kansas City Chiefs", "kc", "AFC"),
    "LA": ("Los Angeles Rams", "lar", "NFC"), "LAC": ("Los Angeles Chargers", "lac", "AFC"),
    "LV": ("Las Vegas Raiders", "lv", "AFC"), "MIA": ("Miami Dolphins", "mia", "AFC"),
    "MIN": ("Minnesota Vikings", "min", "NFC"), "NE": ("New England Patriots", "ne", "AFC"),
    "NO": ("New Orleans Saints", "no", "NFC"), "NYG": ("New York Giants", "nyg", "NFC"),
    "NYJ": ("New York Jets", "nyj", "AFC"), "PHI": ("Philadelphia Eagles", "phi", "NFC"),
    "PIT": ("Pittsburgh Steelers", "pit", "AFC"), "SEA": ("Seattle Seahawks", "sea", "NFC"),
    "SF": ("San Francisco 49ers", "sf", "NFC"), "TB": ("Tampa Bay Buccaneers", "tb", "NFC"),
    "TEN": ("Tennessee Titans", "ten", "AFC"), "WAS": ("Washington Commanders", "wsh", "NFC"),
}

# Stades hors des États-Unis, pour que diffusion.py identifie les droits français.
INTERNATIONAUX = {
    "melbourne cricket ground": ("Melbourne", "Australia"),
    "tottenham hotspur stadium": ("London", "United Kingdom"),
    "wembley stadium": ("London", "United Kingdom"),
    "stade de france": ("Paris", "France"),
    "santiago bernab": ("Madrid", "Spain"),
    "allianz arena": ("Munich", "Germany"),
    "deutsche bank park": ("Frankfurt", "Germany"),
    "estadio azteca": ("Mexico City", "Mexico"),
    "neo química arena": ("São Paulo", "Brazil"),
    "arena corinthians": ("São Paulo", "Brazil"),
    "corinthians arena": ("São Paulo", "Brazil"),
}

TYPES = {"PRE": 1, "REG": 2}
TOURS = {"WC": "Wild Card", "DIV": "Tour de division", "CON": "Finale de conférence", "SB": "Super Bowl"}


def _nombre(valeur) -> float | None:
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return None


def _entier(valeur) -> int | None:
    n = _nombre(valeur)
    return int(n) if n is not None else None


def _equipe(abbr: str, score) -> Team:
    nom, code, _ = EQUIPES.get(abbr, (abbr, "nfl", ""))
    return Team(id=abbr, abbr=abbr, name=nom, logo=LOGO.format(code=code), score=_entier(score))


def _chaine_us(kickoff_est: datetime | None, away: str, game_type: str, semaine: int | None) -> list[str]:
    """Déduit le diffuseur américain. C'est une estimation, jamais une certitude."""
    if kickoff_est is None:
        return []
    jour, minutes = kickoff_est.weekday(), kickoff_est.hour * 60 + kickoff_est.minute
    if game_type not in ("REG", "PRE"):
        return ["FOX, CBS, NBC ou ESPN selon le tour (estimation)"]
    if jour == 3 and minutes >= 19 * 60:
        # Le match d'ouverture de la saison est sur NBC, le TNF hebdomadaire sur Prime Video.
        return ["NBC (estimation)"] if semaine == 1 else ["Prime Video (estimation)"]
    if jour == 6 and minutes >= 19 * 60:
        return ["NBC (estimation)"]
    if jour == 0 and minutes >= 19 * 60:
        return ["ESPN et ABC (estimation)"]
    if jour == 6:
        conference = EQUIPES.get(away, ("", "", "NFC"))[2]
        return ["FOX (estimation)" if conference == "NFC" else "CBS (estimation)"]
    return []


def _ligne_vers_match(ligne: dict) -> Game | None:
    away, home = ligne.get("away_team", ""), ligne.get("home_team", "")
    if not away or not home:
        return None

    kickoff = kickoff_est = None
    jour, heure = ligne.get("gameday", ""), ligne.get("gametime", "")
    if jour:
        try:
            brut = f"{jour} {heure}" if heure else f"{jour} 13:00"
            kickoff_est = datetime.strptime(brut, "%Y-%m-%d %H:%M").replace(tzinfo=EST)
            kickoff = kickoff_est.astimezone(UTC)
        except ValueError:
            log.warning("date illisible : %s %s", jour, heure)

    stade = ligne.get("stadium", "") or ""
    ville, pays = "", ""
    for motif, (v, p) in INTERNATIONAUX.items():
        if motif in stade.lower():
            ville, pays = v, p
            break

    score_dom, score_ext = _entier(ligne.get("home_score")), _entier(ligne.get("away_score"))
    fini = score_dom is not None and score_ext is not None

    game_type = ligne.get("game_type", "REG")
    equipe_dom, equipe_ext = _equipe(home, score_dom), _equipe(away, score_ext)
    if fini:
        equipe_dom.winner = score_dom > score_ext
        equipe_ext.winner = score_ext > score_dom

    game = Game(
        id=ligne.get("game_id", ""),
        kickoff=kickoff,
        season=int(ligne.get("season") or 0),
        season_type=TYPES.get(game_type, 3),
        week=_entier(ligne.get("week")),
        home=equipe_dom,
        away=equipe_ext,
        state="post" if fini else "pre",
        completed=fini,
        detail="Terminé" if fini else "",
        venue=stade,
        venue_city=ville,
        venue_country=pays,
        neutral_site=(ligne.get("location", "") or "").lower() == "neutral",
        us_broadcasts=_chaine_us(kickoff_est, away, game_type, _entier(ligne.get("week"))),
        notes=[TOURS[game_type]] if game_type in TOURS else [],
    )
    game._odds = {  # type: ignore[attr-defined]
        "home": ligne.get("home_moneyline"),
        "away": ligne.get("away_moneyline"),
        "spread": _nombre(ligne.get("spread_line")),
        "total": ligne.get("total_line"),
    }
    return game


class NflverseClient:
    """Télécharge et met en cache les CSV nflverse pour la durée de l'exécution."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._lignes: list[dict] | None = None
        self._stats: dict[int, list[dict]] = {}

    async def _csv(self, url: str) -> list[dict]:
        async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            resp.raise_for_status()
            texte = (await resp.read()).decode("utf-8", errors="replace")
        return list(csv.DictReader(io.StringIO(texte)))

    async def _calendrier_brut(self) -> list[dict]:
        if self._lignes is None:
            self._lignes = await self._csv(CALENDRIER)
            log.info("%d lignes de calendrier téléchargées", len(self._lignes))
        return self._lignes

    async def saison(self, team_id: str, season: int) -> list[Game]:
        lignes = await self._calendrier_brut()
        matchs = [
            m for ligne in lignes
            if str(ligne.get("season")) == str(season)
            and team_id in (ligne.get("home_team"), ligne.get("away_team"))
            for m in [_ligne_vers_match(ligne)] if m
        ]
        return sorted(matchs, key=lambda g: g.kickoff or datetime.max.replace(tzinfo=UTC))

    async def head_to_head(
        self, team_id: str, opponent_id: str, saison_courante: int, seasons_back: int = 6
    ) -> HeadToHead:
        lignes = await self._calendrier_brut()
        h2h = HeadToHead(seasons_covered=seasons_back)
        plancher = saison_courante - seasons_back
        rows: list[tuple[str, str]] = []

        for ligne in lignes:
            equipes = {ligne.get("home_team"), ligne.get("away_team")}
            if equipes != {team_id, opponent_id}:
                continue
            saison = int(ligne.get("season") or 0)
            if saison < plancher or ligne.get("game_type") == "PRE":
                continue
            dom, ext = _entier(ligne.get("home_score")), _entier(ligne.get("away_score"))
            if dom is None or ext is None:
                continue

            chez_nous = ligne.get("home_team") == team_id
            nous, eux = (dom, ext) if chez_nous else (ext, dom)
            h2h.points_for += nous
            h2h.points_against += eux
            if nous > eux:
                h2h.wins += 1
                marque = "V"
            elif nous < eux:
                h2h.losses += 1
                marque = "D"
            else:
                h2h.ties += 1
                marque = "N"
            lieu = "dom." if chez_nous else "ext."
            rows.append((ligne.get("gameday", ""), f"{marque} {nous}:{eux} ({lieu}, {saison})"))

        rows.sort(reverse=True)
        h2h.last_games = [libelle for _, libelle in rows[:5]]
        return h2h

    async def bilan_saison(self, team_id: str, season: int) -> str:
        """Bilan V-D-N de l'équipe sur la saison en cours."""
        v = d = n = 0
        for game in await self.saison(team_id, season):
            if not game.completed or game.season_type != 2:
                continue
            nous, eux = game.side(team_id), game.other(team_id)
            if nous.score is None or eux.score is None:
                continue
            if nous.score > eux.score:
                v += 1
            elif nous.score < eux.score:
                d += 1
            else:
                n += 1
        return f"{v}-{d}" + (f"-{n}" if n else "")

    async def meilleurs_joueurs(self, game: Game) -> dict[str, list[str]]:
        """Meilleur passeur, coureur et receveur de chaque équipe sur ce match."""
        if game.season not in self._stats:
            try:
                self._stats[game.season] = await self._csv(STATS.format(season=game.season))
            except Exception as exc:
                log.warning("statistiques joueurs %s indisponibles : %s", game.season, exc)
                self._stats[game.season] = []

        lignes = [
            l for l in self._stats[game.season]
            if str(l.get("week")) == str(game.week)
            and l.get("team") in (game.home.abbr, game.away.abbr)
        ]
        if not lignes:
            return {}

        resultat: dict[str, list[str]] = {}
        categories = [
            ("passing_yards", "passing_tds", "à la passe"),
            ("rushing_yards", "rushing_tds", "à la course"),
            ("receiving_yards", "receiving_tds", "à la réception"),
        ]
        for equipe in (game.home.abbr, game.away.abbr):
            joueurs = [l for l in lignes if l.get("team") == equipe]
            sortie = []
            for cle_yards, cle_tds, libelle in categories:
                meilleur = max(joueurs, key=lambda l: _nombre(l.get(cle_yards)) or 0, default=None)
                yards = _nombre(meilleur.get(cle_yards)) if meilleur else None
                if not meilleur or not yards:
                    continue
                tds = _entier(meilleur.get(cle_tds)) or 0
                texte = f"{meilleur.get('player_display_name', '?')} · {int(yards)} yds {libelle}"
                if tds:
                    texte += f", {tds} TD"
                sortie.append(texte)
            if sortie:
                resultat[equipe] = sortie
        return resultat


def pronostic(game: Game, team_id: str) -> Prediction:
    """Probabilité de victoire déduite des cotes moneyline, marge du book retirée."""
    pred = Prediction()
    lignes = getattr(game, "_odds", None) or {}
    dom = _implied_from_moneyline(lignes.get("home"))
    ext = _implied_from_moneyline(lignes.get("away"))

    if dom and ext:
        total = dom + ext
        notre = dom if game.is_home(team_id) else ext
        pred.probability = notre / total
        pred.source = "cotes de clôture des bookmakers"

    spread = lignes.get("spread")
    if spread is not None:
        favori = game.home.abbr if float(spread) > 0 else game.away.abbr
        pred.spread = f"{favori} favori de {abs(float(spread))} points"
    if lignes.get("total"):
        pred.over_under = str(lignes["total"])
    return pred

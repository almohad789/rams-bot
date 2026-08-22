"""Où regarder le match, aux États-Unis et en France.

Côté américain, ESPN renseigne directement le diffuseur dans le calendrier.

Côté français, aucune API ne publie la grille NFL. On raisonne donc sur les
droits de la saison 2026 :

  * beIN SPORTS : une affiche par semaine à 19h et une à 22h, tous les matchs
    en prime time (TNF, SNF, MNF), Thanksgiving, le NFL Paris Game,
    l'intégralité des playoffs, le Super Bowl LXI, plus le RedZone le dimanche à 19h.
  * La Chaîne L'Équipe : un match par semaine le dimanche à 22h, un match par
    tour de playoffs, le Super Bowl LXI, les matchs de Madrid et Munich.
  * France Télévisions : le NFL Paris Game et les trois matchs de Londres.
  * NFL Game Pass sur DAZN : toute la saison, en direct et en replay.

beIN et L'Équipe choisissent UNE affiche par créneau, et ce choix n'est
annoncé qu'en cours de semaine. Le bot renvoie donc un niveau de confiance,
jamais une certitude, sauf pour le Game Pass. La table OVERRIDES permet de
figer la chaîne dès que tu la connais.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

GAME_PASS = "NFL Game Pass sur DAZN"

# id de match ESPN -> liste de chaînes françaises confirmées.
# Dès que tu vois la grille beIN ou L'Équipe, ajoute la ligne ici.
OVERRIDES: dict[str, list[str]] = {
    # "401772930": ["beIN SPORTS 1", "La Chaîne L'Équipe"],
}

# Villes des matchs internationaux 2026 et diffuseur français associé.
INTERNATIONAL = {
    "london": (["France Télévisions (france.tv Sport)", "beIN SPORTS"], "match de Londres", "confirmé"),
    "paris": (["France Télévisions", "beIN SPORTS"], "NFL Paris Game", "confirmé"),
    "madrid": (["La Chaîne L'Équipe", "beIN SPORTS"], "match de Madrid", "confirmé"),
    "munich": (["La Chaîne L'Équipe", "beIN SPORTS"], "match de Munich", "confirmé"),
    "münchen": (["La Chaîne L'Équipe", "beIN SPORTS"], "match de Munich", "confirmé"),
    # Melbourne ne figurait pas dans les droits français annoncés pour 2026.
    "melbourne": (["beIN SPORTS"], "match de Melbourne, diffuseur FR non annoncé", "probable"),
}

PRIME_TIME_US = {"NBC", "ESPN", "ABC", "ESPN/ABC", "Prime Video", "Amazon Prime Video", "Netflix"}


@dataclass
class Channel:
    name: str
    confidence: str      # "confirmé", "probable", "possible"
    why: str = ""

    ICONS = {"confirmé": "✅", "probable": "🟡", "possible": "⚪"}

    def render(self) -> str:
        icon = self.ICONS.get(self.confidence, "⚪")
        suffix = f" ({self.why})" if self.why else ""
        return f"{icon} **{self.name}**{suffix}"


@dataclass
class Diffusion:
    usa: list[str] = field(default_factory=list)
    france: list[Channel] = field(default_factory=list)
    note: str = ""

    def usa_text(self) -> str:
        return " / ".join(self.usa) if self.usa else "diffuseur pas encore annoncé"

    def france_text(self) -> str:
        if not self.france:
            return f"✅ **{GAME_PASS}**"
        return "\n".join(c.render() for c in self.france)


def _slot(kickoff_paris: datetime) -> str:
    """Identifie le créneau au sens de la grille française."""
    day = kickoff_paris.weekday()          # lundi = 0
    hour = kickoff_paris.hour
    minute = kickoff_paris.minute
    total = hour * 60 + minute

    # Les matchs de nuit appartiennent à la soirée américaine de la veille.
    if day == 6 and 17 * 60 <= total < 21 * 60:
        return "dimanche_19h"
    if day == 6 and 21 * 60 <= total < 23 * 60 + 59:
        return "dimanche_22h"
    if day == 0 and total < 6 * 60:
        return "sunday_night"
    if day == 1 and total < 6 * 60:
        return "monday_night"
    if day == 4 and total < 6 * 60:
        return "thursday_night"
    if day == 3 and 17 * 60 <= total < 23 * 60 + 59:
        return "thanksgiving_ou_noel"
    return "hors_creneau"


def diffusion_usa(game) -> list[str]:
    if game.us_broadcasts:
        return list(game.us_broadcasts)
    return []


def diffusion_france(game) -> Diffusion:
    """Déduit les chaînes françaises probables à partir du créneau et du contexte."""
    diff = Diffusion(usa=diffusion_usa(game))

    if game.id in OVERRIDES:
        diff.france = [Channel(name, "confirmé", "grille confirmée") for name in OVERRIDES[game.id]]
        diff.france.append(Channel(GAME_PASS, "confirmé", "intégralité de la saison"))
        return diff

    channels: list[Channel] = []
    kickoff = game.kickoff.astimezone(PARIS) if game.kickoff else None

    # 1. Match international : les droits sont attribués nommément.
    city = (game.venue_city or "").lower()
    country = (game.venue_country or "").lower()
    if game.neutral_site or (country and country not in ("usa", "us", "united states")):
        for key, (names, label, niveau) in INTERNATIONAL.items():
            if key in city:
                channels = [Channel(n, niveau, label) for n in names]
                break

    # 2. Playoffs et Super Bowl.
    if not channels and game.season_type == 3:
        note = " ".join(game.notes).lower()
        if "super bowl" in note:
            channels = [
                Channel("beIN SPORTS", "confirmé", "Super Bowl LXI"),
                Channel("La Chaîne L'Équipe", "confirmé", "Super Bowl LXI"),
            ]
        else:
            channels = [
                Channel("beIN SPORTS", "confirmé", "intégralité des playoffs"),
                Channel("La Chaîne L'Équipe", "possible", "un match par tour de playoffs"),
            ]

    # 3. Présaison : rien en clair ni sur les chaînes payantes françaises.
    if not channels and game.season_type == 1:
        diff.note = "Match de présaison : pas de diffusion sur les chaînes françaises, uniquement le Game Pass."
        diff.france = [Channel(GAME_PASS, "confirmé", "direct et replay")]
        return diff

    # 4. Saison régulière : on raisonne par créneau.
    if not channels and kickoff:
        slot = _slot(kickoff)
        us = {b.upper() for b in game.us_broadcasts}
        prime = any(p.upper() in us for p in PRIME_TIME_US)

        if slot == "sunday_night":
            channels = [Channel("beIN SPORTS", "confirmé", "Sunday Night Football")]
        elif slot == "monday_night":
            channels = [Channel("beIN SPORTS", "confirmé", "Monday Night Football")]
        elif slot == "thursday_night":
            channels = [Channel("beIN SPORTS", "confirmé", "Thursday Night Football")]
        elif slot == "thanksgiving_ou_noel":
            label = "Thanksgiving" if kickoff.month == 11 else "match de fin d'année"
            channels = [Channel("beIN SPORTS", "probable", label)]
        elif slot == "dimanche_19h":
            channels = [
                Channel("beIN SPORTS", "possible", "une seule affiche retenue à 19h"),
                Channel("beIN SPORTS RedZone", "confirmé", "tous les dimanches à 19h, en multiplex"),
            ]
        elif slot == "dimanche_22h":
            channels = [
                Channel("beIN SPORTS", "possible", "une seule affiche retenue à 22h"),
                Channel("La Chaîne L'Équipe", "possible", "un match du dimanche 22h par semaine"),
            ]
        elif prime:
            channels = [Channel("beIN SPORTS", "probable", "affiche en prime time US")]

    channels.append(Channel(GAME_PASS, "confirmé", "direct et replay, en VO"))
    diff.france = channels

    if any(c.confidence == "possible" for c in channels):
        diff.note = (
            "beIN et L'Équipe ne retiennent qu'une affiche par créneau et l'annoncent "
            "en cours de semaine. À vérifier sur leur programme la veille du match."
        )
    return diff

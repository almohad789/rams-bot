"""Création des événements planifiés Discord pour les matchs des Rams.

Un webhook ne sait que poster des messages. Les événements planifiés passent par
l'API des serveurs, qui exige un jeton d'application bot et la permission
« Gérer les événements » sur le serveur.

Documentation : POST /guilds/{guild_id}/scheduled-events
Les matchs se déroulant hors de Discord, on crée des événements de type EXTERNAL
(entity_type 3), qui demandent un lieu et une heure de fin.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiohttp

from diffusion import diffusion_france
from presentation import PARIS, fr_datetime

log = logging.getLogger("evenements")

API = "https://discord.com/api/v10"

# Un match NFL dure rarement moins de trois heures, prolongations comprises.
DUREE = timedelta(hours=3, minutes=30)

PRIVACY_GUILD_ONLY = 2
ENTITY_EXTERNAL = 3


def _lieu(game, team_id: str) -> str:
    """Le champ « lieu » de Discord, limité à 100 caractères."""
    diff = diffusion_france(game)
    chaines = [c.name for c in diff.france if "Game Pass" not in c.name]
    if chaines:
        base = " ou ".join(chaines[:2])
    elif diff.usa:
        base = diff.usa[0]
    else:
        base = "NFL Game Pass sur DAZN"
    if game.venue:
        base = f"{base} · {game.venue}"
    return base[:100]


def _nom(game, team_id: str) -> str:
    eux = game.other(team_id)
    prefixe = "🐏 Rams vs" if game.is_home(team_id) else "🐏 Rams à"
    return f"{prefixe} {eux.name}"[:100]


def _description(game, team_id: str, h2h=None, pred=None) -> str:
    diff = diffusion_france(game)
    lignes = [f"{game.season_label}."]

    if game.venue:
        lieu = "à domicile" if game.is_home(team_id) else "à l'extérieur"
        if game.neutral_site:
            lieu = "sur terrain neutre"
        lignes.append(f"{lieu.capitalize()}, {game.venue}.")

    if h2h is not None and h2h.played:
        lignes.append(
            f"Bilan sur les {h2h.seasons_covered} dernières saisons : {h2h.summary}."
        )
    if pred is not None and pred.probability is not None:
        lignes.append(f"Chances de victoire estimées : {pred.percent}. {pred.verdict}")

    lignes.append("")
    lignes.append(f"États-Unis : {diff.usa_text()}")
    lignes.append("France :")
    for c in diff.france:
        marque = {"confirmé": "✅", "probable": "🟡", "possible": "⚪"}.get(c.confidence, "⚪")
        lignes.append(f"{marque} {c.name}" + (f" ({c.why})" if c.why else ""))
    if diff.note:
        lignes.append("")
        lignes.append(diff.note)

    return "\n".join(lignes)[:1000]


async def creer_evenement(
    session: aiohttp.ClientSession,
    token: str,
    guild_id: str,
    game,
    team_id: str,
    h2h=None,
    pred=None,
) -> str | None:
    """Crée l'événement et renvoie son identifiant, ou None en cas d'échec."""
    if not game.kickoff:
        return None

    charge = {
        "name": _nom(game, team_id),
        "privacy_level": PRIVACY_GUILD_ONLY,
        "scheduled_start_time": game.kickoff.isoformat(),
        "scheduled_end_time": (game.kickoff + DUREE).isoformat(),
        "entity_type": ENTITY_EXTERNAL,
        "entity_metadata": {"location": _lieu(game, team_id)},
        "description": _description(game, team_id, h2h, pred),
    }
    entetes = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }

    async with session.post(
        f"{API}/guilds/{guild_id}/scheduled-events", json=charge, headers=entetes
    ) as resp:
        corps = await resp.text()
        if resp.status in (200, 201):
            import json

            identifiant = json.loads(corps).get("id")
            log.info("événement créé pour %s (%s)", game.id, identifiant)
            return identifiant
        if resp.status == 429:
            log.warning("limite de débit Discord atteinte, on réessaiera au prochain passage")
        elif resp.status == 403:
            log.error(
                "permission refusée : le bot doit avoir « Gérer les événements » sur le serveur"
            )
        elif resp.status == 401:
            log.error("jeton de bot invalide : vérifie le secret DISCORD_BOT_TOKEN")
        else:
            log.error("création d'événement refusée (%s) : %s", resp.status, corps[:300])
        return None


async def synchroniser(
    session: aiohttp.ClientSession,
    token: str,
    guild_id: str,
    matchs: list,
    team_id: str,
    etat: dict,
    maintenant,
    horizon_jours: int = 45,
    maximum_par_passage: int = 5,
    enrichir=None,
) -> bool:
    """Crée les événements manquants pour les matchs à venir. Renvoie True si l'état a changé."""
    if not token or not guild_id:
        return False

    modifie = False
    crees = 0
    limite = maintenant + timedelta(days=horizon_jours)

    for game in matchs:
        if crees >= maximum_par_passage:
            log.info("quota de créations atteint pour ce passage, suite au prochain")
            break
        if not game.kickoff or game.completed:
            continue
        if not (maintenant < game.kickoff <= limite):
            continue
        if "evenement" in etat.get(game.id, []):
            continue

        h2h = pred = None
        if enrichir is not None:
            try:
                h2h, pred = await enrichir(game)
            except Exception as exc:
                log.warning("enrichissement impossible pour %s : %s", game.id, exc)

        identifiant = await creer_evenement(
            session, token, guild_id, game, team_id, h2h, pred
        )
        if identifiant:
            etat.setdefault(game.id, []).append("evenement")
            modifie = True
            crees += 1
            await asyncio.sleep(1)          # on reste courtois avec l'API
        else:
            break                            # inutile d'insister si ça échoue

    return modifie

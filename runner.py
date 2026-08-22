"""Bot Rams, exécution unique, pour GitHub Actions.

Les données viennent de nflverse (CSV publiés dans les releases GitHub), et non
plus d'ESPN qui bloque les adresses IP des datacenters.

Usage :
    python runner.py            exécution normale
    python runner.py --test     poste un message tout de suite, pour vérifier
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import discord

from diffusion import diffusion_france
from evenements import synchroniser
from nflverse import NflverseClient, pronostic
from presentation import (
    embed_annonce,
    embed_rappel,
    embed_rapport_nflverse,
    fr_datetime,
    mention_nuit,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("runner")

WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TEAM_ID = os.getenv("TEAM_ID", "LA").strip()          # abréviation nflverse des Rams
ROLE_ID = os.getenv("ROLE_ID", "").strip()

# Facultatif : sans ces deux valeurs, le bot poste les messages mais ne crée
# aucun événement planifié. Un webhook seul n'en a pas le droit.
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
EVENEMENTS_JOURS = int(os.getenv("EVENEMENTS_JOURS", "45"))

ANNONCE_HEURES = float(os.getenv("ANNONCE_HEURES", "48"))
RAPPEL_MINUTES = float(os.getenv("RAPPEL_MINUTES", "75"))
SAISONS_BILAN = int(os.getenv("SAISONS_BILAN", "6"))

ETAT = Path("state.json")


def saison_nfl(maintenant: datetime | None = None) -> int:
    """La saison 2026 court d'août 2026 à février 2027."""
    now = maintenant or datetime.now(timezone.utc)
    return now.year if now.month >= 3 else now.year - 1


def charger_etat() -> dict[str, list[str]]:
    if ETAT.exists():
        try:
            return json.loads(ETAT.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("état illisible (%s), on repart de zéro", exc)
    return {}


def signaler_changement() -> None:
    sortie = os.getenv("GITHUB_OUTPUT")
    if sortie:
        with open(sortie, "a", encoding="utf-8") as f:
            f.write("changed=true\n")


def mention() -> str:
    return f"<@&{ROLE_ID}> " if ROLE_ID else ""


async def poster(session: aiohttp.ClientSession, contenu: str, embed) -> None:
    charge = {
        "content": contenu[:1900],
        "embeds": [embed.to_dict()],
        "allowed_mentions": {"parse": ["roles"] if ROLE_ID else []},
    }
    async with session.post(WEBHOOK, json=charge) as resp:
        if resp.status >= 400:
            corps = await resp.text()
            raise RuntimeError(f"Discord a refusé le message ({resp.status}) : {corps[:300]}")


async def dossier(client: NflverseClient, game):
    adversaire = game.other(TEAM_ID)
    h2h = await client.head_to_head(
        TEAM_ID, adversaire.id, saison_nfl(), seasons_back=SAISONS_BILAN
    )
    return h2h, pronostic(game, TEAM_ID), diffusion_france(game)


async def executer(mode_test: bool = False) -> None:
    if not WEBHOOK:
        raise SystemExit("DISCORD_WEBHOOK_URL absent : ajoute-le dans les secrets du dépôt.")

    etat = charger_etat()
    modifie = False

    async with aiohttp.ClientSession(headers={"User-Agent": "rams-bot/2.0"}) as session:
        client = NflverseClient(session)

        saison = saison_nfl()
        try:
            matchs = await client.saison(TEAM_ID, saison)
        except Exception as exc:
            log.error("calendrier indisponible : %s", exc)
            if mode_test:
                embed = discord.Embed(
                    title="🧪 Test du bot Rams",
                    description="Le webhook fonctionne, mais le calendrier nflverse "
                                f"n'a pas pu être téléchargé.\n`{type(exc).__name__}`",
                    color=0xE67E22,
                )
                await poster(session, "", embed)
            return

        log.info("%d matchs trouvés pour la saison %d", len(matchs), saison)
        maintenant = datetime.now(timezone.utc)

        # ---------------------------------------------------------- mode test
        if mode_test:
            futur = [g for g in matchs if g.kickoff and g.kickoff > maintenant and not g.completed]
            if not futur:
                embed = discord.Embed(
                    title="🧪 Test du bot Rams",
                    description=f"Tout fonctionne, mais aucun match à venir "
                                f"dans la saison {saison}.",
                    color=0xE67E22,
                )
                await poster(session, "", embed)
                return
            game = futur[0]
            h2h, pred, diff = await dossier(client, game)
            await poster(
                session,
                "🧪 Message de test. Le bot est opérationnel.",
                embed_annonce(game, TEAM_ID, h2h, pred, diff),
            )
            log.info("message de test envoyé")
            return

        # ------------------------------------------- événements planifiés
        async def enrichir(game):
            h2h, pred, _ = await dossier(client, game)
            return h2h, pred

        try:
            if await synchroniser(
                session, BOT_TOKEN, GUILD_ID, matchs, TEAM_ID, etat,
                maintenant, horizon_jours=EVENEMENTS_JOURS, enrichir=enrichir,
            ):
                modifie = True
        except Exception as exc:
            log.warning("synchronisation des événements impossible : %s", exc)

        # -------------------------------------------------------- mode normal
        for game in matchs:
            if not game.kickoff:
                continue
            deja = etat.get(game.id, [])
            delta = game.kickoff - maintenant

            if (
                not game.completed
                and timedelta(0) < delta <= timedelta(hours=ANNONCE_HEURES)
                and "annonce" not in deja
            ):
                h2h, pred, diff = await dossier(client, game)
                heure = fr_datetime(game.kickoff) + mention_nuit(game.kickoff)
                await poster(
                    session,
                    f"{mention()}🐏 Les Rams jouent {heure} contre "
                    f"**{game.other(TEAM_ID).name}**.",
                    embed_annonce(game, TEAM_ID, h2h, pred, diff),
                )
                etat.setdefault(game.id, []).append("annonce")
                modifie = True
                log.info("annonce postée : %s", game.id)

            if (
                not game.completed
                and timedelta(0) < delta <= timedelta(minutes=RAPPEL_MINUTES)
                and "rappel" not in deja
            ):
                await poster(
                    session,
                    f"{mention()}⏰ Ça commence bientôt.",
                    embed_rappel(game, TEAM_ID, diffusion_france(game)),
                )
                etat.setdefault(game.id, []).append("rappel")
                modifie = True
                log.info("rappel posté : %s", game.id)

            if game.completed and "rapport" not in deja:
                joueurs = await client.meilleurs_joueurs(game)
                bilan = await client.bilan_saison(TEAM_ID, game.season)
                await poster(session, "", embed_rapport_nflverse(game, TEAM_ID, joueurs, bilan))
                etat.setdefault(game.id, []).append("rapport")
                modifie = True
                log.info("rapport posté : %s", game.id)

    if modifie:
        ETAT.write_text(json.dumps(etat, ensure_ascii=False, indent=2), encoding="utf-8")
        signaler_changement()
        log.info("état mis à jour")
    else:
        log.info("rien à publier cette fois")


if __name__ == "__main__":
    asyncio.run(executer(mode_test="--test" in sys.argv))

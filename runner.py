"""Version « une seule exécution » du bot, pour GitHub Actions.

Contrairement à bot.py qui tourne en permanence, ce script se réveille,
regarde s'il y a quelque chose à annoncer, poste le cas échéant, puis s'arrête.
GitHub Actions le relance toutes les 15 minutes.

Il poste via un webhook Discord : pas besoin de créer une application bot,
ni de l'inviter sur le serveur.

Usage :
    python runner.py            exécution normale
    python runner.py --test     poste le prochain match tout de suite, pour vérifier
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

from diffusion import diffusion_france
from espn import EspnClient, Game, Prediction, prediction_from_summary
from presentation import embed_annonce, embed_rappel, embed_rapport, fr_datetime, mention_nuit

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("runner")

WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TEAM_ID = os.getenv("TEAM_ID", "14").strip()
ROLE_ID = os.getenv("ROLE_ID", "").strip()

ANNONCE_HEURES = float(os.getenv("ANNONCE_HEURES", "48"))
RAPPEL_MINUTES = float(os.getenv("RAPPEL_MINUTES", "75"))
SAISONS_BILAN = int(os.getenv("SAISONS_BILAN", "6"))

ETAT = Path("state.json")


def saison_nfl(maintenant: datetime | None = None) -> int:
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
    """Prévient le workflow qu'il doit committer state.json."""
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


async def dossier(espn: EspnClient, game: Game):
    adversaire = game.other(TEAM_ID)
    h2h = await espn.head_to_head(TEAM_ID, adversaire.id, saison_nfl(), seasons_back=SAISONS_BILAN)
    try:
        summary = await espn.summary(game.id)
        pred = prediction_from_summary(summary, TEAM_ID, game.home.id)
    except Exception as exc:
        log.warning("fiche match indisponible : %s", exc)
        pred = Prediction()
    return h2h, pred, diffusion_france(game)


async def executer(mode_test: bool = False) -> None:
    if not WEBHOOK:
        raise SystemExit("DISCORD_WEBHOOK_URL absent : ajoute-le dans les secrets du dépôt.")

    etat = charger_etat()
    modifie = False

    async with aiohttp.ClientSession(headers={"User-Agent": "rams-bot/1.0"}) as session:
        espn = EspnClient(session)

        saison = saison_nfl()
        matchs = await espn.full_season(TEAM_ID, saison)
        if not matchs:
            matchs = await espn.full_season(TEAM_ID, saison - 1)
        log.info("%d matchs récupérés pour la saison %d", len(matchs), saison)

        maintenant = datetime.now(timezone.utc)

        if mode_test:
            futur = [g for g in matchs if g.kickoff and g.kickoff > maintenant and not g.completed]
            if not futur:
                log.warning("aucun match à venir, rien à tester")
                return
            game = futur[0]
            h2h, pred, diff = await dossier(espn, game)
            await poster(
                session,
                "🧪 Message de test, tout fonctionne.",
                embed_annonce(game, TEAM_ID, h2h, pred, diff),
            )
            log.info("message de test envoyé")
            return

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
                h2h, pred, diff = await dossier(espn, game)
                heure = fr_datetime(game.kickoff) + mention_nuit(game.kickoff)
                await poster(
                    session,
                    f"{mention()}🐏 Les Rams jouent {heure} contre **{game.other(TEAM_ID).name}**.",
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
                summary = await espn.summary(game.id, ttl=30)
                await poster(session, "", embed_rapport(game, TEAM_ID, summary))
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

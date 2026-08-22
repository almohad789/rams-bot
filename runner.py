"""Version « une seule exécution » du bot, pour GitHub Actions.
 
Contrairement à bot.py qui tourne en permanence, ce script se réveille,
regarde s'il y a quelque chose à annoncer, poste le cas échéant, puis s'arrête.
GitHub Actions le relance toutes les 15 minutes.
 
Il poste via un webhook Discord : pas besoin de créer une application bot,
ni de l'inviter sur le serveur.
 
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
from espn import SITE, EspnClient, Game, Prediction, prediction_from_summary
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
 
# ESPN filtre les clients qui ne ressemblent pas à un navigateur. Un nom de bot
# se fait renvoyer un 403, y compris depuis les serveurs de GitHub.
NAVIGATEUR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Referer": "https://www.espn.com/nfl/team/schedule/_/name/lar",
    "Origin": "https://www.espn.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}
 
 
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
    # Le webhook Discord n'a rien à faire des en-têtes destinés à ESPN.
    async with session.post(WEBHOOK, json=charge, headers={"User-Agent": "rams-bot"}) as resp:
        if resp.status >= 400:
            corps = await resp.text()
            raise RuntimeError(f"Discord a refusé le message ({resp.status}) : {corps[:300]}")
 
 
async def sonder_espn(session: aiohttp.ClientSession) -> str:
    """Interroge ESPN une fois et renvoie un verdict lisible."""
    url = f"{SITE}/teams/{TEAM_ID}/schedule"
    try:
        async with session.get(
            url, params={"season": saison_nfl()}, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                return f"✅ ESPN répond, {len(data.get('events') or [])} matchs dans le calendrier"
            return f"❌ ESPN renvoie une erreur {resp.status}"
    except Exception as exc:
        return f"❌ ESPN injoignable : {type(exc).__name__}"
 
 
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
 
    async with aiohttp.ClientSession(headers=NAVIGATEUR) as session:
        espn = EspnClient(session)
 
        saison = saison_nfl()
        matchs = await espn.full_season(TEAM_ID, saison)
        if not matchs:
            matchs = await espn.full_season(TEAM_ID, saison - 1)
        log.info("%d matchs récupérés pour la saison %d", len(matchs), saison)
 
        maintenant = datetime.now(timezone.utc)
 
        # ------------------------------------------------------------------
        # Mode test : on poste TOUJOURS quelque chose, pour valider le webhook
        # même quand ESPN ne répond pas.
        # ------------------------------------------------------------------
        if mode_test:
            futur = [g for g in matchs if g.kickoff and g.kickoff > maintenant and not g.completed]
            if futur:
                game = futur[0]
                h2h, pred, diff = await dossier(espn, game)
                await poster(
                    session,
                    "🧪 Message de test. Le webhook et les données ESPN fonctionnent.",
                    embed_annonce(game, TEAM_ID, h2h, pred, diff),
                )
                log.info("message de test envoyé avec le prochain match")
                return
 
            verdict = await sonder_espn(session)
            embed = discord.Embed(
                title="🧪 Test du bot Rams",
                description="Le webhook Discord fonctionne : ce message en est la preuve.\n"
                            "En revanche, aucun match n'a pu être récupéré.",
                color=0xE67E22,
            )
            embed.add_field(name="Source de données", value=verdict, inline=False)
            embed.add_field(
                name="Ce que ça veut dire",
                value="Si ESPN renvoie une erreur 403, le serveur qui exécute le bot est filtré. "
                      "Le bot continuera de tourner et réessaiera à chaque exécution.",
                inline=False,
            )
            embed.set_footer(text=f"Saison visée : {saison}")
            await poster(session, "", embed)
            log.info("message de diagnostic envoyé")
            return
 
        # ------------------------------------------------------------------
        # Mode normal
        # ------------------------------------------------------------------
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
 

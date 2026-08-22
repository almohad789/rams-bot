"""Bot Discord de suivi des Los Angeles Rams.

Il publie automatiquement :
  1. une annonce 48 heures avant le coup d'envoi (adversaire, horaire de Paris,
     bilan face à cette équipe, probabilité de victoire, chaînes US et France) ;
  2. un rappel une heure avant, avec les chaînes ;
  3. un rapport complet à la fin du match.

Lancement : python bot.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from diffusion import diffusion_france
from espn import EspnClient, Game, prediction_from_summary
from presentation import (
    PARIS,
    embed_annonce,
    embed_rappel,
    embed_rapport,
    fr_datetime,
    mention_nuit,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s : %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rams")

TOKEN = os.getenv("DISCORD_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0") or 0)
ROLE_ID = os.getenv("ROLE_ID", "").strip()
TEAM_ID = os.getenv("TEAM_ID", "14").strip()          # 14 = Los Angeles Rams
GUILD_ID = os.getenv("GUILD_ID", "").strip()          # facultatif : sync instantanée des commandes

ANNONCE_HEURES = float(os.getenv("ANNONCE_HEURES", "48"))
RAPPEL_MINUTES = float(os.getenv("RAPPEL_MINUTES", "60"))
INTERVALLE_MINUTES = float(os.getenv("INTERVALLE_MINUTES", "5"))
SAISONS_BILAN = int(os.getenv("SAISONS_BILAN", "6"))

ETAT = Path(os.getenv("STATE_FILE", "state.json"))


def saison_nfl(maintenant: datetime | None = None) -> int:
    """La saison 2026 court d'août 2026 à février 2027."""
    now = maintenant or datetime.now(timezone.utc)
    return now.year if now.month >= 3 else now.year - 1


class Etat:
    """Mémorise ce qui a déjà été envoyé, pour ne rien poster deux fois."""

    def __init__(self, chemin: Path):
        self.chemin = chemin
        self.envoyes: dict[str, list[str]] = {}
        if chemin.exists():
            try:
                self.envoyes = json.loads(chemin.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("état illisible (%s), on repart de zéro", exc)

    def deja(self, event_id: str, genre: str) -> bool:
        return genre in self.envoyes.get(event_id, [])

    def marquer(self, event_id: str, genre: str) -> None:
        self.envoyes.setdefault(event_id, []).append(genre)
        try:
            self.chemin.write_text(
                json.dumps(self.envoyes, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.error("impossible d'écrire l'état : %s", exc)

    def purger(self, ids_actifs: set[str]) -> None:
        """Évite que le fichier gonfle indéfiniment saison après saison."""
        if len(self.envoyes) > 400:
            self.envoyes = {k: v for k, v in self.envoyes.items() if k in ids_actifs}


class RamsBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.session: aiohttp.ClientSession | None = None
        self.espn: EspnClient | None = None
        self.etat = Etat(ETAT)
        self.equipes: dict[str, dict] = {}

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": "rams-discord-bot/1.0"}
        )
        self.espn = EspnClient(self.session)
        enregistrer_commandes(self)
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.surveillance.start()

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

    async def on_ready(self):
        log.info("connecté en tant que %s", self.user)
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="les Rams 🐏")
        )

    # ------------------------------------------------------------------
    # Récupération des données
    # ------------------------------------------------------------------

    async def calendrier(self) -> list[Game]:
        assert self.espn
        saison = saison_nfl()
        matchs = await self.espn.full_season(TEAM_ID, saison)
        if not matchs:                       # intersaison très précoce
            matchs = await self.espn.full_season(TEAM_ID, saison - 1)
        return matchs

    async def prochain_match(self) -> Game | None:
        maintenant = datetime.now(timezone.utc)
        for game in await self.calendrier():
            if game.kickoff and game.kickoff > maintenant - timedelta(hours=4) and not game.completed:
                return game
        return None

    async def dernier_match(self) -> Game | None:
        joues = [g for g in await self.calendrier() if g.completed and g.kickoff]
        return joues[-1] if joues else None

    async def dossier(self, game: Game):
        """Bilan + pronostic + diffusion pour un match donné."""
        assert self.espn
        adversaire = game.other(TEAM_ID)
        h2h = await self.espn.head_to_head(
            TEAM_ID, adversaire.id, saison_nfl(), seasons_back=SAISONS_BILAN
        )
        try:
            summary = await self.espn.summary(game.id)
            pred = prediction_from_summary(summary, TEAM_ID, game.home.id)
        except Exception as exc:
            log.warning("fiche match %s indisponible : %s", game.id, exc)
            from espn import Prediction

            pred = Prediction()
        return h2h, pred, diffusion_france(game)

    # ------------------------------------------------------------------
    # Boucle de surveillance
    # ------------------------------------------------------------------

    async def salon(self) -> discord.abc.Messageable | None:
        if not CHANNEL_ID:
            log.error("CHANNEL_ID absent du fichier .env")
            return None
        salon = self.get_channel(CHANNEL_ID)
        if salon is None:
            try:
                salon = await self.fetch_channel(CHANNEL_ID)
            except discord.DiscordException as exc:
                log.error("salon %s inaccessible : %s", CHANNEL_ID, exc)
                return None
        return salon

    def mention(self) -> str:
        return f"<@&{ROLE_ID}> " if ROLE_ID else ""

    @tasks.loop(minutes=INTERVALLE_MINUTES)
    async def surveillance(self):
        try:
            matchs = await self.calendrier()
        except Exception as exc:
            log.error("calendrier indisponible : %s", exc)
            return

        salon = await self.salon()
        if salon is None:
            return

        maintenant = datetime.now(timezone.utc)
        self.etat.purger({g.id for g in matchs})

        for game in matchs:
            if not game.kickoff:
                continue
            delta = game.kickoff - maintenant

            # 1. Annonce à J-2
            if (
                not game.completed
                and timedelta(0) < delta <= timedelta(hours=ANNONCE_HEURES)
                and not self.etat.deja(game.id, "annonce")
            ):
                await self._publier_annonce(salon, game)

            # 2. Rappel une heure avant
            if (
                not game.completed
                and timedelta(0) < delta <= timedelta(minutes=RAPPEL_MINUTES)
                and not self.etat.deja(game.id, "rappel")
            ):
                await self._publier_rappel(salon, game)

            # 3. Rapport de fin de match
            if game.completed and not self.etat.deja(game.id, "rapport"):
                await self._publier_rapport(salon, game)

    @surveillance.before_loop
    async def avant_surveillance(self):
        await self.wait_until_ready()

    async def _publier_annonce(self, salon, game: Game):
        try:
            h2h, pred, diff = await self.dossier(game)
            embed = embed_annonce(game, TEAM_ID, h2h, pred, diff)
            heure = fr_datetime(game.kickoff) + mention_nuit(game.kickoff)
            await salon.send(
                f"{self.mention()}🐏 Les Rams jouent {heure} contre "
                f"**{game.other(TEAM_ID).name}**.",
                embed=embed,
            )
            self.etat.marquer(game.id, "annonce")
            log.info("annonce publiée pour %s", game.id)
        except Exception as exc:
            log.exception("échec de l'annonce %s : %s", game.id, exc)

    async def _publier_rappel(self, salon, game: Game):
        try:
            await salon.send(
                f"{self.mention()}⏰ Coup d'envoi dans une heure.",
                embed=embed_rappel(game, TEAM_ID, diffusion_france(game)),
            )
            self.etat.marquer(game.id, "rappel")
            log.info("rappel publié pour %s", game.id)
        except Exception as exc:
            log.exception("échec du rappel %s : %s", game.id, exc)

    async def _publier_rapport(self, salon, game: Game):
        assert self.espn
        try:
            summary = await self.espn.summary(game.id, ttl=30)
            await salon.send(embed=embed_rapport(game, TEAM_ID, summary))
            self.etat.marquer(game.id, "rapport")
            log.info("rapport publié pour %s", game.id)
        except Exception as exc:
            log.exception("échec du rapport %s : %s", game.id, exc)


# ----------------------------------------------------------------------
# Commandes slash
# ----------------------------------------------------------------------


def enregistrer_commandes(bot: RamsBot):
    @bot.tree.command(name="prochain", description="Le prochain match des Rams, en détail")
    async def prochain(interaction: discord.Interaction):
        await interaction.response.defer()
        game = await bot.prochain_match()
        if game is None:
            await interaction.followup.send("Aucun match programmé pour le moment.")
            return
        h2h, pred, diff = await bot.dossier(game)
        await interaction.followup.send(embed=embed_annonce(game, TEAM_ID, h2h, pred, diff))

    @bot.tree.command(name="calendrier", description="Les prochains matchs des Rams")
    @app_commands.describe(nombre="Combien de matchs afficher (1 à 10)")
    async def calendrier(interaction: discord.Interaction, nombre: int = 5):
        await interaction.response.defer()
        nombre = max(1, min(10, nombre))
        maintenant = datetime.now(timezone.utc)
        a_venir = [
            g for g in await bot.calendrier()
            if g.kickoff and g.kickoff > maintenant and not g.completed
        ][:nombre]

        if not a_venir:
            await interaction.followup.send("Plus de match au calendrier.")
            return

        embed = discord.Embed(title="🗓️ Calendrier des Rams", color=0x003594)
        for g in a_venir:
            adversaire = g.other(TEAM_ID)
            prefixe = "vs" if g.is_home(TEAM_ID) else "@"
            diff = diffusion_france(g)
            embed.add_field(
                name=f"{prefixe} {adversaire.name}",
                value=f"{fr_datetime(g.kickoff)}{mention_nuit(g.kickoff)}\n"
                      f"🇺🇸 {diff.usa_text()}\n{g.season_label}",
                inline=False,
            )
        embed.set_footer(text="Heures de Paris")
        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="bilan", description="Bilan des Rams face à une équipe")
    @app_commands.describe(equipe="Nom ou abréviation, par exemple 49ers, SEA, Cardinals")
    async def bilan(interaction: discord.Interaction, equipe: str):
        await interaction.response.defer()
        assert bot.espn

        if not bot.equipes:
            data = await bot.espn._get(
                "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams", ttl=86400
            )
            for entry in (
                data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
            ):
                t = entry.get("team", {})
                bot.equipes[str(t.get("id"))] = t

        recherche = equipe.lower().strip()
        cible = None
        for t in bot.equipes.values():
            champs = [
                str(t.get("abbreviation", "")).lower(),
                str(t.get("name", "")).lower(),
                str(t.get("displayName", "")).lower(),
                str(t.get("location", "")).lower(),
            ]
            if any(recherche == c for c in champs) or any(recherche in c and c for c in champs):
                cible = t
                break

        if cible is None:
            await interaction.followup.send(f"Équipe introuvable : {equipe}")
            return

        h2h = await bot.espn.head_to_head(
            TEAM_ID, str(cible.get("id")), saison_nfl(), seasons_back=SAISONS_BILAN
        )
        embed = discord.Embed(
            title=f"📊 Rams face aux {cible.get('displayName')}",
            description=f"**{h2h.summary}** sur les {SAISONS_BILAN} dernières saisons",
            color=0x003594,
        )
        if h2h.played:
            embed.add_field(
                name="Points",
                value=f"{h2h.points_for} marqués · {h2h.points_against} encaissés "
                      f"({h2h.points_for / h2h.played:.1f} contre {h2h.points_against / h2h.played:.1f} par match)",
                inline=False,
            )
        if h2h.last_games:
            embed.add_field(name="Dernières confrontations", value="\n".join(h2h.last_games), inline=False)
        if cible.get("logos"):
            embed.set_thumbnail(url=cible["logos"][0].get("href", ""))
        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="dernier", description="Le rapport du dernier match joué")
    async def dernier(interaction: discord.Interaction):
        await interaction.response.defer()
        game = await bot.dernier_match()
        if game is None:
            await interaction.followup.send("Aucun match terminé dans le calendrier.")
            return
        assert bot.espn
        summary = await bot.espn.summary(game.id)
        await interaction.followup.send(embed=embed_rapport(game, TEAM_ID, summary))


def main():
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN manquant : renseigne le fichier .env")
    if not CHANNEL_ID:
        raise SystemExit("CHANNEL_ID manquant : renseigne le fichier .env")
    RamsBot().run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

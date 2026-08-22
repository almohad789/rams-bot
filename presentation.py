"""Construction des messages Discord : dates en français et embeds."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord

from espn import Game, HeadToHead, Prediction, _dig
from diffusion import Diffusion

PARIS = ZoneInfo("Europe/Paris")

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

COULEUR_RAMS = 0x003594
COULEUR_VICTOIRE = 0x2ECC71
COULEUR_DEFAITE = 0xE74C3C
COULEUR_NULLE = 0x95A5A6


def fr_datetime(dt: datetime | None, avec_annee: bool = False) -> str:
    if dt is None:
        return "horaire non communiqué"
    local = dt.astimezone(PARIS)
    jour = JOURS[local.weekday()]
    mois = MOIS[local.month - 1]
    annee = f" {local.year}" if avec_annee else ""
    return f"{jour} {local.day} {mois}{annee} à {local:%Hh%M}"


def mention_nuit(dt: datetime | None) -> str:
    """Signale les coups d'envoi au milieu de la nuit, très fréquents depuis Paris."""
    if dt is None:
        return ""
    local = dt.astimezone(PARIS)
    if 0 <= local.hour < 6:
        veille = local - timedelta(days=1)
        return f" (nuit de {JOURS[veille.weekday()]} à {JOURS[local.weekday()]})"
    return ""


def timestamp_discord(dt: datetime | None) -> str:
    """Horodatage natif Discord : chacun le voit dans son fuseau."""
    if dt is None:
        return ""
    return f"<t:{int(dt.timestamp())}:F> ({'<t:%d:R>' % int(dt.timestamp())})"


def _titre_match(game: Game, team_id: str) -> str:
    nous, eux = game.side(team_id), game.other(team_id)
    if game.is_home(team_id):
        return f"{nous.name} 🆚 {eux.name}"
    return f"{eux.name} 🆚 {nous.name}"


def _lieu(game: Game, team_id: str) -> str:
    domicile = "à domicile" if game.is_home(team_id) else "à l'extérieur"
    if game.neutral_site:
        domicile = "sur terrain neutre"
    lieu = game.venue or "stade non communiqué"
    if game.venue_city:
        lieu += f", {game.venue_city}"
    return f"{domicile} · {lieu}"


def embed_annonce(
    game: Game,
    team_id: str,
    h2h: HeadToHead,
    pred: Prediction,
    diff: Diffusion,
    titre: str = "🏈 Prochain match des Rams",
) -> discord.Embed:
    nous, eux = game.side(team_id), game.other(team_id)

    embed = discord.Embed(
        title=titre,
        description=f"**{_titre_match(game, team_id)}**\n{game.season_label}",
        color=COULEUR_RAMS,
    )
    if eux.logo:
        embed.set_thumbnail(url=eux.logo)

    embed.add_field(
        name="🗓️ Coup d'envoi (heure de Paris)",
        value=f"{fr_datetime(game.kickoff, avec_annee=True)}{mention_nuit(game.kickoff)}\n"
              f"{timestamp_discord(game.kickoff)}",
        inline=False,
    )

    forme = []
    if nous.record:
        forme.append(f"Rams : {nous.record}")
    if eux.record:
        forme.append(f"{eux.abbr or eux.name} : {eux.record}")
    embed.add_field(
        name="📍 Où",
        value=_lieu(game, team_id) + (("\n" + " · ".join(forme)) if forme else ""),
        inline=False,
    )

    # Bilan face à l'adversaire
    lignes = [f"**{h2h.summary}** sur les {h2h.seasons_covered} dernières saisons"]
    if h2h.played:
        moy_pour = h2h.points_for / h2h.played
        moy_contre = h2h.points_against / h2h.played
        lignes.append(f"Moyenne : {moy_pour:.1f} points marqués, {moy_contre:.1f} encaissés")
    if h2h.last_games:
        lignes.append("Dernières confrontations : " + " · ".join(h2h.last_games))
    embed.add_field(name=f"📊 Bilan face aux {eux.name}", value="\n".join(lignes), inline=False)

    # Pronostic
    pronostic = [f"**{pred.percent}** de chances de victoire pour les Rams"]
    if pred.source:
        pronostic.append(f"_source : {pred.source}_")
    if pred.spread:
        pronostic.append(f"Spread : {pred.spread}" + (f" · total {pred.over_under}" if pred.over_under else ""))
    pronostic.append(pred.verdict)
    embed.add_field(name="🎯 Pronostic", value="\n".join(pronostic), inline=False)

    embed.add_field(name="🇺🇸 Diffusion aux États-Unis", value=diff.usa_text(), inline=False)
    embed.add_field(name="🇫🇷 Diffusion en France", value=diff.france_text(), inline=False)
    if diff.note:
        embed.add_field(name="ℹ️ À savoir", value=diff.note, inline=False)

    embed.set_footer(text="Données ESPN · droits de diffusion saison NFL 2026")
    return embed


def embed_rappel(game: Game, team_id: str, diff: Diffusion) -> discord.Embed:
    eux = game.other(team_id)
    embed = discord.Embed(
        title="⏰ Ça commence dans une heure",
        description=f"**{_titre_match(game, team_id)}**\n"
                    f"{fr_datetime(game.kickoff)}{mention_nuit(game.kickoff)} heure de Paris",
        color=COULEUR_RAMS,
    )
    if eux.logo:
        embed.set_thumbnail(url=eux.logo)
    embed.add_field(name="🇺🇸 États-Unis", value=diff.usa_text(), inline=False)
    embed.add_field(name="🇫🇷 France", value=diff.france_text(), inline=False)
    return embed


def _stat(equipe_stats: list, cle: str) -> str:
    for s in equipe_stats or []:
        if s.get("name") == cle:
            return str(s.get("displayValue", ""))
    return ""


def embed_rapport(game: Game, team_id: str, summary: dict) -> discord.Embed:
    """Rapport de fin de match : score, quart-temps, stats clés, leaders."""
    nous, eux = game.side(team_id), game.other(team_id)
    score_nous = nous.score if nous.score is not None else 0
    score_eux = eux.score if eux.score is not None else 0

    if score_nous > score_eux:
        couleur, entete = COULEUR_VICTOIRE, "✅ Victoire des Rams"
    elif score_nous < score_eux:
        couleur, entete = COULEUR_DEFAITE, "❌ Défaite des Rams"
    else:
        couleur, entete = COULEUR_NULLE, "🤝 Match nul"

    ecart = abs(score_nous - score_eux)
    if ecart == 0:
        commentaire = "Personne ne prend l'avantage."
    elif ecart <= 3:
        commentaire = "Décidé au bout du suspense."
    elif ecart <= 10:
        commentaire = "Match maîtrisé sans être tranquille."
    else:
        commentaire = "Écart net au tableau d'affichage."

    embed = discord.Embed(
        title=entete,
        description=f"**{nous.name} {score_nous} : {score_eux} {eux.name}**\n"
                    f"{game.season_label} · {commentaire}",
        color=couleur,
    )

    # Score par quart-temps
    competitors = _dig(summary, "header", "competitions", 0, "competitors", default=[]) or []
    lignes_qt = []
    for c in competitors:
        abbr = _dig(c, "team", "abbreviation", default="?")
        qts = [str(_dig(ls, "displayValue", default=_dig(ls, "value", default="-")))
               for ls in (c.get("linescores") or [])]
        if qts:
            lignes_qt.append(f"`{abbr:<4}` " + " · ".join(qts) + f"  →  **{c.get('score', '?')}**")
    if lignes_qt:
        embed.add_field(name="🕐 Quart-temps", value="\n".join(lignes_qt), inline=False)

    # Statistiques d'équipe
    equipes = _dig(summary, "boxscore", "teams", default=[]) or []
    interessantes = [
        ("totalYards", "Yards totaux"),
        ("netPassingYards", "Yards passe"),
        ("rushingYards", "Yards course"),
        ("firstDowns", "First downs"),
        ("thirdDownEff", "3e tentative"),
        ("turnovers", "Turnovers"),
        ("possessionTime", "Possession"),
        ("totalPenaltiesYards", "Pénalités"),
    ]
    if len(equipes) >= 2:
        gauche = next((e for e in equipes if _dig(e, "team", "id") == team_id), equipes[0])
        droite = next((e for e in equipes if e is not gauche), equipes[1])
        g_abbr = _dig(gauche, "team", "abbreviation", default="LAR")
        d_abbr = _dig(droite, "team", "abbreviation", default="ADV")
        lignes = [f"`{'':<16}{g_abbr:>8}{d_abbr:>8}`"]
        for cle, libelle in interessantes:
            gv = _stat(gauche.get("statistics"), cle)
            dv = _stat(droite.get("statistics"), cle)
            if gv or dv:
                lignes.append(f"`{libelle:<16}{gv:>8}{dv:>8}`")
        if len(lignes) > 1:
            embed.add_field(name="📈 Statistiques", value="\n".join(lignes), inline=False)

    # Meilleurs joueurs
    for bloc in summary.get("leaders") or []:
        abbr = _dig(bloc, "team", "abbreviation", default="")
        lignes = []
        for cat in bloc.get("leaders") or []:
            if cat.get("name") not in ("passingYards", "rushingYards", "receivingYards"):
                continue
            tete = (cat.get("leaders") or [{}])[0]
            joueur = _dig(tete, "athlete", "displayName", default="")
            valeur = tete.get("displayValue", "")
            if joueur:
                lignes.append(f"{joueur} · {valeur}")
        if lignes:
            embed.add_field(name=f"⭐ {abbr}", value="\n".join(lignes), inline=True)

    article = (summary.get("article") or {}).get("description") or ""
    if article:
        embed.add_field(name="📰 Résumé ESPN", value=article[:1000], inline=False)

    embed.set_footer(text="Rapport de fin de match · données ESPN")
    return embed

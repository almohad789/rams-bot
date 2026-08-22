"""Vérifie la logique hors ligne, avec des données ESPN simulées.

Lancer : python test_local.py
"""

from datetime import datetime, timezone

from diffusion import diffusion_france
from espn import HeadToHead, Prediction, parse_event, prediction_from_summary
from presentation import embed_annonce, embed_rapport, fr_datetime, mention_nuit

RAMS = "14"


def evenement(event_id, date_iso, adversaire, adv_id, dom=True, chaines=None,
              season_type=2, ville="Inglewood", pays="USA", neutre=False, notes=None):
    rams = {"homeAway": "home" if dom else "away", "winner": None,
            "score": {"value": 0}, "team": {"id": RAMS, "abbreviation": "LAR",
            "displayName": "Los Angeles Rams", "logo": ""},
            "records": [{"type": "total", "summary": "2-0"}]}
    adv = {"homeAway": "away" if dom else "home", "winner": None,
           "score": {"value": 0}, "team": {"id": adv_id, "abbreviation": adversaire[:3].upper(),
           "displayName": adversaire, "logo": ""},
           "records": [{"type": "total", "summary": "1-1"}]}
    return {
        "id": event_id, "date": date_iso, "season": {"year": 2026, "type": season_type},
        "week": {"number": 3},
        "competitions": [{
            "date": date_iso,
            "neutralSite": neutre,
            "competitors": [rams, adv],
            "broadcasts": [{"media": {"shortName": c}} for c in (chaines or [])],
            "status": {"type": {"state": "pre", "completed": False, "detail": "Scheduled"}},
            "venue": {"fullName": "SoFi Stadium", "address": {"city": ville, "country": pays}},
            "notes": [{"headline": n} for n in (notes or [])],
        }],
    }


CAS = [
    ("Dimanche 19h Paris (créneau 13h ET)",
     evenement("1", "2026-10-04T17:00Z", "Philadelphia Eagles", "21", dom=False, chaines=["FOX"])),
    ("Dimanche 22h Paris (créneau 16h ET)",
     evenement("2", "2026-10-18T20:05Z", "Arizona Cardinals", "22", chaines=["FOX"])),
    ("Monday Night Football",
     evenement("3", "2026-10-13T00:15Z", "Buffalo Bills", "2", chaines=["ESPN"])),
    ("Sunday Night Football",
     evenement("4", "2026-09-28T00:20Z", "Denver Broncos", "7", dom=False, chaines=["NBC"])),
    ("Thursday Night Football",
     evenement("5", "2026-12-04T01:15Z", "Kansas City Chiefs", "12", chaines=["Prime Video"])),
    ("Thanksgiving",
     evenement("6", "2026-11-26T01:00Z", "Green Bay Packers", "9", chaines=["NBC"])),
    ("Présaison",
     evenement("7", "2026-08-22T20:00Z", "New Orleans Saints", "18", season_type=1)),
    ("Playoffs",
     evenement("8", "2027-01-17T21:30Z", "Seattle Seahawks", "26", season_type=3,
               chaines=["FOX"], notes=["NFC Divisional"])),
    ("Match à Londres",
     evenement("9", "2026-10-11T13:30Z", "Jacksonville Jaguars", "30", dom=False,
               chaines=["NFL Network"], ville="London", pays="United Kingdom", neutre=True)),
]

print("=" * 74)
print("DIFFUSION : créneau → chaînes déduites")
print("=" * 74)
for libelle, brut in CAS:
    game = parse_event(brut)
    assert game is not None, libelle
    diff = diffusion_france(game)
    heure = fr_datetime(game.kickoff) + mention_nuit(game.kickoff)
    print(f"\n▸ {libelle}")
    print(f"  {heure}")
    print(f"  USA    : {diff.usa_text()}")
    for ligne in diff.france_text().split("\n"):
        print(f"  France : {ligne}")

print("\n" + "=" * 74)
print("PRONOSTIC : conversion des cotes et du Matchup Predictor")
print("=" * 74)

predictor = {"predictor": {"homeTeam": {"id": "14", "gameProjection": "63.4"},
                           "awayTeam": {"id": "26", "gameProjection": "36.6"}},
             "pickcenter": [{"provider": {"name": "ESPN BET"}, "details": "LAR -3.5",
                             "overUnder": 47.5,
                             "homeTeamOdds": {"moneyLine": -175},
                             "awayTeamOdds": {"moneyLine": 145}}]}
p = prediction_from_summary(predictor, RAMS, "14")
print(f"Predictor   : {p.percent} via {p.source} · {p.spread} · {p.verdict}")

cotes_seules = {"pickcenter": predictor["pickcenter"]}
p2 = prediction_from_summary(cotes_seules, RAMS, "14")
print(f"Cotes seules: {p2.percent} via {p2.source} · {p2.verdict}")

p3 = prediction_from_summary(cotes_seules, RAMS, "26")   # Rams à l'extérieur
print(f"Rams ext.   : {p3.percent} · {p3.verdict}")
print(f"Contrôle    : somme = {p2.probability + p3.probability:.3f} (doit valoir 1.000)")

print("\n" + "=" * 74)
print("EMBED D'ANNONCE")
print("=" * 74)

game = parse_event(CAS[1][1])
h2h = HeadToHead(wins=7, losses=3, points_for=272, points_against=213,
                 last_games=["V 37:20 (dom., 2026)", "D 17:24 (ext., 2025)"], seasons_covered=6)
embed = embed_annonce(game, RAMS, h2h, p, diffusion_france(game))
print(f"Titre : {embed.title}")
print(f"Corps : {embed.description}")
for f in embed.fields:
    print(f"\n[{f.name}]\n{f.value}")

print("\n" + "=" * 74)
print("EMBED DE RAPPORT")
print("=" * 74)

fini = evenement("2", "2026-10-18T20:05Z", "Arizona Cardinals", "22", chaines=["FOX"])
fini["competitions"][0]["competitors"][0]["score"] = {"value": 31}
fini["competitions"][0]["competitors"][1]["score"] = {"value": 24}
fini["competitions"][0]["status"] = {"type": {"state": "post", "completed": True, "detail": "Final"}}
game_fini = parse_event(fini)

summary = {
    "header": {"competitions": [{"competitors": [
        {"team": {"abbreviation": "LAR"}, "score": "31",
         "linescores": [{"displayValue": "7"}, {"displayValue": "10"},
                        {"displayValue": "7"}, {"displayValue": "7"}]},
        {"team": {"abbreviation": "ARI"}, "score": "24",
         "linescores": [{"displayValue": "3"}, {"displayValue": "14"},
                        {"displayValue": "0"}, {"displayValue": "7"}]},
    ]}]},
    "boxscore": {"teams": [
        {"team": {"id": "14", "abbreviation": "LAR"}, "statistics": [
            {"name": "totalYards", "displayValue": "412"},
            {"name": "netPassingYards", "displayValue": "287"},
            {"name": "rushingYards", "displayValue": "125"},
            {"name": "firstDowns", "displayValue": "24"},
            {"name": "thirdDownEff", "displayValue": "7-13"},
            {"name": "turnovers", "displayValue": "1"},
            {"name": "possessionTime", "displayValue": "32:14"},
            {"name": "totalPenaltiesYards", "displayValue": "5-40"}]},
        {"team": {"id": "22", "abbreviation": "ARI"}, "statistics": [
            {"name": "totalYards", "displayValue": "355"},
            {"name": "netPassingYards", "displayValue": "268"},
            {"name": "rushingYards", "displayValue": "87"},
            {"name": "firstDowns", "displayValue": "19"},
            {"name": "thirdDownEff", "displayValue": "4-12"},
            {"name": "turnovers", "displayValue": "2"},
            {"name": "possessionTime", "displayValue": "27:46"},
            {"name": "totalPenaltiesYards", "displayValue": "8-65"}]},
    ]},
    "leaders": [
        {"team": {"abbreviation": "LAR"}, "leaders": [
            {"name": "passingYards", "leaders": [
                {"athlete": {"displayName": "M. Stafford"}, "displayValue": "287 YDS, 3 TD"}]},
            {"name": "rushingYards", "leaders": [
                {"athlete": {"displayName": "K. Williams"}, "displayValue": "98 YDS, 1 TD"}]}]},
    ],
    "article": {"description": "Les Rams s'imposent au terme d'un quatrième quart-temps maîtrisé."},
}

rapport = embed_rapport(game_fini, RAMS, summary)
print(f"Titre : {rapport.title}")
print(f"Corps : {rapport.description}")
for f in rapport.fields:
    print(f"\n[{f.name}]\n{f.value}")

print("\n✅ Tous les cas passent.")

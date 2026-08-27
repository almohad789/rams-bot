# 🐏 Bot Discord Los Angeles Rams

Prévient automatiquement quand les Rams jouent : horaire de Paris, adversaire,
bilan face à cette équipe, probabilité de victoire, chaînes américaines et
françaises, puis rapport de fin de match. Il crée aussi les **événements
planifiés** du serveur Discord pour toute la saison.

Tourne **sur GitHub Actions**, sans serveur, sans machine allumée.

---

## ⚙️ Comment il tourne

`runner.py` est exécuté **toutes les 15 minutes** par le workflow
`.github/workflows/rams.yml`. À chaque passage il télécharge le calendrier,
regarde ce qui est dû, poste ce qui manque, puis s'arrête.

Les données viennent de **nflverse** (fichiers CSV publiés dans les releases
GitHub) et non d'ESPN : ESPN filtre les adresses IP des datacenters, donc son
API est inutilisable depuis un runner Actions.

`state.json` mémorise ce qui a déjà été publié pour chaque match (`annonce`,
`rappel`, `rapport`, `evenement`). Le workflow le recommite dès qu'il change,
ce qui garantit zéro doublon d'une exécution à l'autre. Les clés sont les
identifiants nflverse, du type `2026_01_SF_LA`.

---

## 📣 Ce que le bot publie tout seul

| Quand              | Contenu |
| ------------------ | ------- |
| **48 h avant**     | Adversaire, date et heure de Paris, lieu, bilan des 6 dernières saisons face à cette équipe, probabilité de victoire, spread, chaînes US et France |
| **75 min avant**   | Rappel court avec les chaînes |
| **Fin du match**   | Score final, meilleurs passeur, coureur et receveur de chaque équipe, bilan de la saison en cours |
| **En continu**     | Un événement planifié Discord par match à venir |

Les délais se règlent par variables d'environnement (`ANNONCE_HEURES`,
`RAPPEL_MINUTES`), voir la table de configuration plus bas.

---

## 📅 Événements planifiés Discord

Un webhook ne sait que poster des messages. Pour créer de vrais événements dans
l'onglet **Événements** du serveur, il faut une application bot :

1. `DISCORD_BOT_TOKEN` : jeton de l'application (onglet **Bot** →
   **Reset Token** sur [le portail développeur](https://discord.com/developers/applications)).
2. `DISCORD_GUILD_ID` : identifiant du serveur (mode développeur activé, clic
   droit sur le serveur → **Copier l'identifiant**).
3. Le bot doit être invité sur le serveur avec la permission
   **Gérer les événements**.

Sans ces deux valeurs, le bot fonctionne normalement mais ne crée aucun
événement. Les événements sont de type **externe** (le match ne se déroule pas
sur Discord), durée fixée à 3h30, lieu rempli avec la chaîne française probable
et le stade. Chaque passage en crée au maximum **5**, pour rester poli avec
l'API : la saison complète se remplit donc en quelques passages.

---

## 🔧 Configuration

Dans *Settings → Secrets and variables → Actions*.

**Secrets**

| Nom                   | Obligatoire | Rôle |
| --------------------- | ----------- | ---- |
| `DISCORD_WEBHOOK_URL` | oui         | webhook du salon où le bot poste |
| `DISCORD_BOT_TOKEN`   | non         | jeton bot, uniquement pour les événements planifiés |
| `DISCORD_GUILD_ID`    | non         | identifiant du serveur, idem |

**Variables**

| Nom       | Défaut | Rôle |
| --------- | ------ | ---- |
| `ROLE_ID` | vide   | rôle à mentionner dans les annonces et rappels |

**Réglages dans le workflow** (`env:` du job, à modifier directement dans
`rams.yml`)

| Nom                | Défaut dans le code | Valeur du workflow | Rôle |
| ------------------ | ------------------- | ------------------ | ---- |
| `TEAM_ID`          | `LA`                | `LA`               | abréviation nflverse de l'équipe suivie |
| `EVENEMENTS_JOURS` | `45`                | `365`              | horizon de création des événements |
| `ANNONCE_HEURES`   | `48`                | non défini         | délai de l'annonce, en heures |
| `RAPPEL_MINUTES`   | `75`                | non défini         | délai du rappel, en minutes |
| `SAISONS_BILAN`    | `6`                 | non défini         | profondeur du bilan face à l'adversaire |

⚠️ `TEAM_ID` utilise les abréviations **nflverse**, pas les identifiants ESPN.
Les Rams sont `LA` (et non `LAR`). Le dictionnaire `EQUIPES` en haut de
`nflverse.py` liste les 32 codes.

---

## 🧪 Tester

Onglet **Actions** → **Bot Rams** → **Run workflow** → cocher
*Poster un message de test tout de suite*. Le bot poste l'annonce complète du
prochain match, sans toucher à `state.json`. Si le calendrier nflverse est
injoignable, il poste quand même un message signalant que le webhook fonctionne.

En local :

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python runner.py --test
```

---

## 📡 Sources de données

| Fichier nflverse                 | Sert à |
| -------------------------------- | ------ |
| `schedules/games.csv`            | calendrier complet, horaires, stades, scores finaux, cotes moneyline et spread, historique depuis 1999 |
| `stats_player/stats_player_week_{saison}.csv` | meilleurs joueurs du match |

Ce que nflverse ne fournit pas : la **chaîne américaine**. Elle est déduite du
créneau horaire et de la conférence de l'équipe visiteuse (jeudi soir → Prime
Video, dimanche soir → NBC, lundi soir → ESPN et ABC, dimanche après midi → FOX
ou CBS selon la conférence). C'est affiché explicitement comme une estimation.

---

## 🇫🇷 Diffusion française : la limite à connaître

Droits NFL en France pour la saison 2026 :

* **beIN SPORTS** : une affiche par semaine à 19h et une à 22h, tous les matchs
  en prime time (TNF, SNF, MNF), Thanksgiving, le NFL Paris Game, l'intégralité
  des playoffs, le Super Bowl LXI, et le RedZone le dimanche à 19h.
* **La Chaîne L'Équipe** : un match par semaine le dimanche à 22h, un match par
  tour de playoffs, le Super Bowl LXI, les matchs de Madrid et Munich.
* **France Télévisions** : le NFL Paris Game et les trois matchs de Londres.
* **NFL Game Pass sur DAZN** : toute la saison, en direct et en replay.

Problème : beIN et L'Équipe ne retiennent **qu'une** affiche par créneau, et ce
choix n'est publié nulle part sous forme exploitable par un programme. Le bot
affiche donc un niveau de confiance :

* ✅ certain (prime time, playoffs, match international, Game Pass)
* 🟡 probable
* ⚪ possible, une affiche parmi plusieurs sur le créneau

Dès que la grille est connue, figer la chaîne dans `OVERRIDES` en haut de
`diffusion.py`. La clé est l'**identifiant nflverse** du match :

```python
OVERRIDES = {
    "2026_01_SF_LA": ["beIN SPORTS 1", "La Chaîne L'Équipe"],
}
```

---

## 🎲 Probabilité de victoire

Déduite des **cotes moneyline de clôture** publiées par nflverse, marge du
bookmaker retirée. Le spread et le total sont affichés à côté quand ils existent.

C'est un indicateur, pas une prédiction fiable. Aucun modèle ne bat le marché,
et un pourcentage à 63 % veut dire qu'une défaite sur trois reste normale.

---

## 📁 Structure du dépôt

```
rams-bot/
├── runner.py          # point d'entrée GitHub Actions, exécution unique
├── nflverse.py        # téléchargement et lecture des CSV, pronostic, stats joueurs
├── evenements.py      # création des événements planifiés Discord
├── presentation.py    # construction des embeds et formatage des dates FR
├── diffusion.py       # chaînes US et FR, niveaux de confiance, OVERRIDES
├── espn.py            # dataclasses partagées + ancien client ESPN
├── bot.py             # version historique auto-hébergée (voir plus bas)
├── test_local.py      # vérification hors ligne de l'ancienne chaîne ESPN
├── state.json         # ce qui a déjà été publié (auto, à garder avec le code)
└── .github/workflows/
    └── rams.yml       # exécution toutes les 15 minutes
```

---

## 🗄️ Version historique : `bot.py`

`bot.py` est la première version : un bot Discord **connecté en permanence**,
avec des commandes slash (`/prochain`, `/calendrier`, `/bilan`, `/dernier`) et
des données ESPN. Elle est conservée pour qui voudrait l'héberger sur un
Raspberry Pi ou un VPS, avec un fichier `.env` et un service systemd.

Elle **ne fonctionne pas sur GitHub Actions** : ESPN bloque les IP de
datacenter, et un runner ne peut pas rester connecté à la passerelle Discord.
`espn.py` et `test_local.py` appartiennent à cette version ; `espn.py` reste
importé par `nflverse.py` pour ses dataclasses (`Game`, `Team`, `HeadToHead`,
`Prediction`).

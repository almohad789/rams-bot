# Bot Discord Los Angeles Rams

Prévient automatiquement quand les Rams jouent, avec l'horaire de Paris,
l'adversaire, le bilan face à cette équipe, une probabilité de victoire,
les chaînes américaines et françaises, puis un rapport de fin de match.

## Ce que le bot publie tout seul

| Quand | Contenu |
|---|---|
| 48 h avant | Adversaire, date et heure de Paris, lieu, bilan des 6 dernières saisons face à cette équipe, probabilité de victoire, spread, chaînes US et France |
| 1 h avant | Rappel court avec les chaînes |
| Fin du match | Score, quart-temps, statistiques d'équipe, meilleurs joueurs, résumé |

Les délais se règlent dans le `.env` (`ANNONCE_HEURES`, `RAPPEL_MINUTES`).

## Commandes

* `/prochain` : le prochain match en détail
* `/calendrier [nombre]` : les prochains matchs
* `/bilan <équipe>` : bilan des Rams face à une équipe donnée
* `/dernier` : rapport du dernier match joué

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Créer le bot Discord

1. Va sur https://discord.com/developers/applications puis **New Application**.
2. Onglet **Bot** : **Reset Token**, copie le jeton dans `DISCORD_TOKEN`.
3. Onglet **OAuth2 > URL Generator** : coche `bot` et `applications.commands`,
   puis dans les permissions `Send Messages`, `Embed Links`, `Read Message History`.
   Ouvre l'URL générée pour inviter le bot sur ton serveur.
4. Dans Discord, active le mode développeur (Paramètres > Avancés), clic droit
   sur le salon voulu, **Copier l'identifiant**, colle dans `CHANNEL_ID`.
5. Facultatif : `ROLE_ID` pour mentionner un rôle, `GUILD_ID` pour que les
   commandes slash apparaissent immédiatement au lieu d'attendre jusqu'à une heure.

### Lancer

```bash
python bot.py
```

Vérifier la logique sans Discord ni réseau :

```bash
python test_local.py
```

## Hébergement

Le bot doit tourner en continu. Trois options simples :

* **Un Raspberry Pi ou un vieux PC chez toi**, avec un service systemd.
* **Un VPS à quelques euros par mois** (Hetzner, OVH, Scaleway).
* **Railway ou Fly.io**, qui suffisent largement pour cette charge.

Exemple de service systemd :

```ini
[Unit]
Description=Bot Discord Rams
After=network-online.target

[Service]
WorkingDirectory=/opt/rams-bot
ExecStart=/opt/rams-bot/.venv/bin/python bot.py
Restart=always
RestartSec=30
User=rams

[Install]
WantedBy=multi-user.target
```

Le fichier `state.json` mémorise ce qui a déjà été publié : si le bot redémarre,
il ne republie pas les mêmes messages. Garde ce fichier avec le code.

## Source des données

L'API publique ESPN, gratuite et sans clé, mais **non documentée et non
garantie**. Le client met tout en cache (30 minutes pour le calendrier,
2 minutes pour un match en cours) et retente trois fois en cas d'erreur.
Si ESPN change ses champs, le parsing est écrit en défensif : le bot
dégrade l'information au lieu de planter.

## Diffusion française : la limite à connaître

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

Dès que tu connais la grille, fige la chaîne dans `OVERRIDES` en haut de
`diffusion.py` :

```python
OVERRIDES = {
    "401772930": ["beIN SPORTS 1", "La Chaîne L'Équipe"],
}
```

L'identifiant est celui d'ESPN, visible dans l'URL de la fiche du match.

## Probabilité de victoire

Le bot prend en priorité le **Matchup Predictor** d'ESPN. S'il n'est pas encore
publié, il déduit la probabilité de la cote moneyline en retirant la marge du
bookmaker. La source utilisée est toujours indiquée sous le pourcentage.

C'est un indicateur, pas une prédiction fiable. Aucun modèle ne bat le marché,
et un pourcentage à 63 % veut dire qu'une défaite sur trois reste normale.

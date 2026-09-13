# Guide de contribution — Groupe 2

Règles de travail pour le binôme, en cohérence avec la Roadmap et le contrat d'interface
défini dans le Cadrage (§4).

## Organisation des branches

- `main` : toujours déployable, protégée.
- `develop` : intégration continue des deux sous-projets.
- Branches de travail : `feature/rfm-<sujet>` (Abdoulmadjid), `feature/analytics-<sujet>` (Maslaw),
  `feature/common-<sujet>` pour le socle mutualisé.

## Avant d'ouvrir une Pull Request

1. `make lint` et `make test` passent localement.
2. Si vous touchez `app/models/client.py` ou `app/models/transaction.py` (tables partagées),
   prévenez le binôme avant de merger — un renommage de colonne casse silencieusement l'autre
   sous-projet (cf. skill `pme-marketing-rfm` §5 et §6).
3. Toute nouvelle colonne/table doit être répercutée dans `scripts/init_db.sql` **et** dans une
   migration Alembic (`alembic revision --autogenerate -m "..."`).
4. Mettre à jour `README.md` si un endpoint ou l'architecture change.

## Répartition des dossiers (rappel RACI, Cadrage §3)

| Dossier | Responsable principal |
|---|---|
| `app/services/analytics_service.py`, `app/routers/analytics.py` | Maslaw |
| `app/services/marketing_service.py`, `app/routers/marketing.py` | Abdoulmadjid |
| `app/models/`, `app/core/`, `docker-compose.yml`, `scripts/init_db.sql` | Mutualisé — co-responsabilité |
| `app/services/copilot_service.py`, `app/routers/copilot.py` | Mutualisé (Maslaw lead) |

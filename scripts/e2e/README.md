# Test de bout en bout (navigateur réel + LLM simulé)

Rejoue le parcours complet d'un utilisateur dans Chromium : inscription,
import des 3 fichiers, segmentation, ventes & stocks, copilote (avec une panne
du LLM simulée), rapport PDF, déconnexion. Le copilote est branché sur un faux
fournisseur LLM compatible OpenAI (`mock_llm.py`) : aucune clé ni accès
Internet nécessaire.

## 1. Lancer le LLM simulé

```bash
python scripts/e2e/mock_llm.py          # écoute sur le port 9100
```

Le comportement se change à chaud en écrivant dans `scripts/e2e/mode` :
`ok`, `erreur500`, `cle_invalide`, `vide`, `malforme`, `lent` (25 s, au-delà
du délai de 20 s du client). Chaque requête reçue est enregistrée dans
`scripts/e2e/requetes.jsonl` (clé, modèle, prompt, contexte envoyé).

## 2. Brancher l'API dessus (`.env`)

```
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-test-simule
LLM_MODEL=modele-simule
LLM_BASE_URL=http://host.docker.internal:9100/v1   # API dans Docker
# LLM_BASE_URL=http://127.0.0.1:9100/v1            # API lancée en local
```

Puis `docker compose up -d --build` (sous Linux, ajouter
`extra_hosts: ["host.docker.internal:host-gateway"]` au service `api`).

## 3. Lancer le parcours

```bash
pip install playwright && playwright install chromium
python -m app.ingestion.export_adventureworks         # données de test
E2E_URL=http://localhost:3000 E2E_DONNEES=data/adventureworks python scripts/e2e/parcours.py
```

Résultats dans `e2e_resultats/` : journal horodaté
(`resultat_parcours.json`), captures de chaque écran, rapport PDF téléchargé.
Code de sortie 1 si une requête de l'application a échoué (HTTP ≥ 400).

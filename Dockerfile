# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# gcc + g++ + make : filet de sécurité si un paquet doit être compilé depuis
# ses sources faute de wheel pour la plateforme (CmdStan, lui, n'est plus
# compilé au build : cf. plus bas).
#
# libglib2.0-0, libpango-1.0-0, libpangoft2-1.0-0, libharfbuzz0b,
# libharfbuzz-subset0, libfontconfig1, fonts-liberation : bibliothèques que
# WeasyPrint charge dynamiquement (dlopen) au RUNTIME pour générer le
# rapport PDF (POST /api/v1/report/pdf) — absentes, l'endpoint échoue avec
# une OSError de chargement de bibliothèque au premier appel, malgré un
# `pip install weasyprint` qui réussit sans erreur (cf. README §2).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    libpq-dev \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libharfbuzz-subset0 \
    libfontconfig1 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# BUG CORRIGÉ : la version précédente utilisait `pip install --no-cache-dir`,
# qui DÉSACTIVE explicitement le cache pip — chaque rebuild retéléchargeait
# ALORS systématiquement tous les paquets depuis PyPI, même quand
# requirements.txt n'avait pas changé et que la couche Docker aurait dû
# suffire à elle seule. On utilise au contraire un cache PERSISTANT entre
# builds (`--mount=type=cache`, nécessite BuildKit — activé par défaut avec
# `docker compose` v2 ; sur `docker-compose` v1, exporter
# DOCKER_BUILDKIT=1 et COMPOSE_DOCKER_CLI_BUILD=1 au préalable, cf. README §7).
# Ce cache survit même quand la couche Docker elle-même doit être
# reconstruite (ex. après `--no-cache`, ou changement de requirements.txt) :
# les .whl déjà téléchargés sont réutilisés, seul PyPI est sollicité pour
# les paquets réellement nouveaux ou mis à jour.
#
# --default-timeout=120 / --retries 10 : le timeout par défaut de pip
# (quelques secondes d'inactivité entre deux chunks) est trop court sur une
# connexion lente ou instable ; on l'augmente et on relance automatiquement
# un téléchargement coupé au lieu d'abandonner immédiatement (ReadTimeoutError).
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=120 --retries 10 -r requirements.txt

# xgboost est installé À PART, avec --no-deps : son wheel PyPI déclare
# `nvidia-nccl-cu12` (bibliothèque de communication multi-GPU Nvidia, ~340 Mo)
# comme dépendance, alors qu'elle n'est utile qu'à l'entraînement DISTRIBUÉ
# sur plusieurs GPU — jamais le cas ici (XGBRegressor en CPU classique, cf.
# app/services/forecasting_service.py). La télécharger a déjà fait échouer
# un build avec une OSError disque (téléchargement de 340 Mo pour rien, sur
# un environnement à l'espace disque limité). --no-deps saute cette
# dépendance ; ses deux VRAIES dépendances (numpy, scipy) sont de toute
# façon déjà installées ci-dessus via requirements.txt/scikit-learn.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=120 --retries 10 --no-deps xgboost==2.1.1

# CmdStan (moteur C++ de Prophet) : AUCUNE compilation au build.
# CORRIGÉ (13/10/2026) — l'image compilait CmdStan depuis les sources
# (`cmdstanpy.install_cmdstan`, 10 à 20 minutes de build sur 2 cœurs) puis
# supprimait la copie de CmdStan fournie dans le paquet prophet 1.1.5. Ce
# contournement compensait en réalité un problème de version : cmdstanpy
# n'était pas figé, et cmdstanpy 1.3.0 refuse le CmdStan embarqué par prophet
# 1.1.5 (« CmdStan installation missing makefile »). cmdstanpy étant figé à
# 1.2.4 (requirements.txt), Prophet utilise directement sa copie embarquée,
# déjà compilée : rien à télécharger ni à compiler.
#
# Vérification au build : on ENTRAÎNE réellement un petit modèle Prophet
# (plus sûr qu'une simple instanciation : on vérifie aussi l'exécutable
# CmdStan). Le build échoue ici, plutôt qu'en pleine démonstration, si les
# dépendances deviennent incompatibles.
RUN python -c "import pandas as pd; from prophet import Prophet; \
df = pd.DataFrame({'ds': pd.date_range('2024-01-01', periods=30), 'y': range(30)}); \
Prophet(daily_seasonality=False, yearly_seasonality=False).fit(df); print('Prophet + CmdStan : OK')"

# Copié APRÈS l'installation des dépendances : un changement de code
# applicatif (le cas le plus fréquent en développement) n'invalide donc
# JAMAIS les couches pip install / vérification Prophet ci-dessus, qui restent servies
# depuis le cache Docker normal (pas seulement le cache BuildKit).
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

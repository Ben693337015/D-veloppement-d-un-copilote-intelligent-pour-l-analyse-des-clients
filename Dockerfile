# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# gcc + g++ + make : requis pour compiler les extensions C de numpy/scipy/
# statsmodels ET pour CmdStan (backend C++ de Prophet, cf. cmdstanpy). Sans
# g++/make, l'installation de CmdStan échoue en boucle silencieuse et le
# build peut sembler "bloqué" pendant plusieurs minutes.
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

# Compile CmdStan UNE FOIS au moment du build de l'image (couche mise en
# cache par Docker tant que requirements.txt ne change pas, donc jamais
# reconstruite lors d'un simple changement de code applicatif — cf. l'ordre
# des instructions COPY ci-dessous), plutôt que de laisser Prophet le faire
# paresseusement au premier appel API en production — ce qui bloquerait la
# première requête pendant plusieurs minutes et pourrait dépasser le
# timeout du client.

RUN python -c "import cmdstanpy; cmdstanpy.install_cmdstan(cores=2)"

# BUG CONNU (prophet==1.1.5) : le package embarque sa propre copie locale de
# CmdStan (prophet/stan_model/cmdstan-2.33.1), téléchargée séparément lors
# du `pip install prophet` ci-dessus, INDÉPENDAMMENT de l'installation
# globale ci-dessus. Si ce téléchargement est interrompu (souci réseau
# pendant le build), le dossier existe mais est incomplet — et Prophet
# l'utilise quand même en priorité (il vérifie seulement que le dossier
# EXISTE, pas qu'il est complet, cf. prophet/models.py ligne 93), plutôt
# que de se rabattre sur l'installation globale ci-dessus qui, elle,
# fonctionne. On supprime donc systématiquement cette copie locale pour
# forcer l'usage de l'installation globale vérifiée, et on fait échouer le
# build immédiatement si Prophet ne parvient toujours pas à démarrer —
# plutôt que de le découvrir en pleine démonstration.
RUN rm -rf $(python -c "import importlib.resources as r; print(r.files('prophet') / 'stan_model' / 'cmdstan-2.33.1')")
RUN python -c "from prophet import Prophet; Prophet(); print('Prophet + CmdStan : OK')"

# Copié APRÈS l'installation des dépendances : un changement de code
# applicatif (le cas le plus fréquent en développement) n'invalide donc
# JAMAIS les couches pip install / CmdStan ci-dessus, qui restent servies
# depuis le cache Docker normal (pas seulement le cache BuildKit).
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

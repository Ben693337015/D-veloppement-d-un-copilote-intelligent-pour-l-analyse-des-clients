"""Sécurité — hachage de mot de passe (bcrypt) et tokens JWT (Phase 4).

Choix technique : `bcrypt` utilisé DIRECTEMENT plutôt que via `passlib`
(le duo classique des tutoriels FastAPI). `passlib==1.7.4` (dernière
version publiée, projet non maintenu depuis 2020) est cassé avec les
versions récentes de `bcrypt` (>=4.1, qui a retiré l'attribut
`__about__.__version__` que passlib essaie de lire pour détecter le
backend) : `pip install "passlib[bcrypt]"` installe une combinaison qui
lève une `ValueError` surprenante ("password cannot be longer than 72
bytes") dès le premier hash, y compris sur un mot de passe court —
vérifié en environnement de test avant de choisir cette dépendance (cf.
mémoire projet : "toujours vérifier une dépendance avant de l'ajouter",
même principe que la découverte du besoin de précompiler CmdStan).
`bcrypt` seul a une API suffisante (`hashpw`/`checkpw`) et est activement
maintenu.

Pour le JWT : `python-jose[cryptography]`, la bibliothèque JWT la plus
utilisée avec FastAPI (recommandée par la documentation officielle).
"""

from datetime import datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

ALGORITHME_JWT = "HS256"
# bcrypt tronque silencieusement au-delà de 72 octets — on refuse plutôt
# qu'on ne tronque en silence un mot de passe trop long (comportement
# surprenant sinon : deux mots de passe différents au-delà de 72 octets
# mais identiques sur les 72 premiers seraient acceptés comme identiques).
LONGUEUR_MAX_MOT_DE_PASSE = 72


def hash_password(mot_de_passe: str) -> str:
    if len(mot_de_passe.encode("utf-8")) > LONGUEUR_MAX_MOT_DE_PASSE:
        raise ValueError(f"Le mot de passe ne doit pas dépasser {LONGUEUR_MAX_MOT_DE_PASSE} octets.")
    sel = bcrypt.gensalt()
    return bcrypt.hashpw(mot_de_passe.encode("utf-8"), sel).decode("utf-8")


def verify_password(mot_de_passe: str, hache: str) -> bool:
    try:
        return bcrypt.checkpw(mot_de_passe.encode("utf-8"), hache.encode("utf-8"))
    except (ValueError, TypeError):
        # Hash mal formé/corrompu en base -> échec de vérification, jamais
        # une exception qui remonterait jusqu'au client HTTP.
        return False


def create_access_token(subject: str) -> str:
    expiration = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expiration}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHME_JWT)


def decode_access_token(token: str) -> str | None:
    """Retourne le `sub` (email) du token si valide et non expiré, sinon
    None — ne lève jamais d'exception, laisse l'appelant décider de la
    réponse HTTP (401) plutôt que de coupler ce module à FastAPI."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHME_JWT])
    except JWTError:
        return None
    return payload.get("sub")

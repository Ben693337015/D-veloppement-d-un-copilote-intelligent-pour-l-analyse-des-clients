"""Dépendance FastAPI — utilisateur courant à partir du token JWT (Phase 4).

Utilisée comme `dependencies=[Depends(get_current_user)]` sur chaque router
protégé (analytics, marketing, ingestion, copilot, report) : suffit à
protéger TOUTES les routes du router sans les modifier une par une. Le
routeur `/health` (racine, hors `/api/v1`) reste volontairement PUBLIC —
utilisé par le frontend pour son indicateur "API connectée" avant même
qu'un utilisateur soit connecté (cf. `frontend/src/hooks/useApiHealth.js`).
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import User

# tokenUrl : chemin RELATIF à la racine de l'app (pas au router courant) —
# doit correspondre exactement à la route de login réelle, uniquement
# utilisé pour générer le bouton "Authorize" dans Swagger /docs.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

_EXCEPTION_NON_AUTHENTIFIE = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Non authentifié — jeton manquant, invalide ou expiré.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    if token is None:
        raise _EXCEPTION_NON_AUTHENTIFIE
    email = decode_access_token(token)
    if email is None:
        raise _EXCEPTION_NON_AUTHENTIFIE
    utilisateur = db.query(User).filter(User.email == email).first()
    if utilisateur is None or not utilisateur.actif:
        raise _EXCEPTION_NON_AUTHENTIFIE
    return utilisateur

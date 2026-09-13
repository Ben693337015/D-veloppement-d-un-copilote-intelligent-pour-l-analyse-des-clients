from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas.auth import Token, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["Authentification"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """Création de compte.

    Volontairement OUVERTE (pas de clé d'invitation ni de validation par un
    administrateur) — cohérent avec le périmètre "simple" choisi pour cette
    itération (un outil interne à une petite équipe PME, pas une plateforme
    multi-tenant grand public). À restreindre avant tout déploiement exposé
    publiquement (ex. désactiver la route après la création des comptes
    initiaux, ou exiger une clé d'invitation) — hors périmètre académique
    de cette itération, signalé explicitement plutôt que laissé implicite.
    """
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Un compte existe déjà avec cet email.")
    utilisateur = User(
        email=payload.email,
        nom=payload.nom,
        mot_de_passe_hache=hash_password(payload.mot_de_passe),
    )
    db.add(utilisateur)
    db.commit()
    db.refresh(utilisateur)
    return utilisateur


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Compatible `OAuth2PasswordRequestForm` (form-urlencoded username/
    password, PAS du JSON) — standard FastAPI qui permet, entre autres,
    d'utiliser directement le bouton "Authorize" de Swagger `/docs` pour
    tester l'API protégée sans outil externe."""
    utilisateur = db.query(User).filter(User.email == form_data.username.strip().lower()).first()
    if utilisateur is None or not verify_password(form_data.password, utilisateur.mot_de_passe_hache):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")
    if not utilisateur.actif:
        raise HTTPException(status_code=403, detail="Compte désactivé.")
    return Token(access_token=create_access_token(subject=utilisateur.email))


@router.get("/me", response_model=UserOut)
def me(utilisateur: User = Depends(get_current_user)):
    return utilisateur

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.core.security import hash_password
from app.main import app
from app.models import (  # noqa: F401 (registers ORM mappers)
    Client,
    StockProduit,
    Transaction,
    Tresorerie,
    User,
)

# Base SQLite en mémoire pour les tests — ne touche jamais au PostgreSQL réel.
# StaticPool : une seule connexion partagée, sinon chaque nouvelle connexion
# SQLite ":memory:" repart d'une base vide (piège classique du "no such table").
TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def test_user(db_session):
    """Utilisateur authentifié réel, persisté en base (mot de passe
    RÉELLEMENT haché via bcrypt, pas un simulacre) — sert de support à
    l'override d'authentification du fixture `client` ci-dessous, pour que
    toute la suite existante (analytics/marketing/ingestion/copilot/report)
    reste protégée par une vraie dépendance FastAPI sans avoir à refaire un
    login HTTP explicite dans chacun de ses dizaines de tests. Le flux de
    login/register lui-même, LUI, est testé sans ce raccourci — cf.
    `client_sans_auth` et `tests/test_auth_router.py`."""
    utilisateur = User(
        email="test@copilote-pme.local",
        nom="Utilisateur de test",
        mot_de_passe_hache=hash_password("mot-de-passe-test-123"),
    )
    db_session.add(utilisateur)
    db_session.commit()
    db_session.refresh(utilisateur)
    return utilisateur


@pytest.fixture(scope="function")
def client(db_session, test_user):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: test_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def client_sans_auth(db_session):
    """Variante SANS override de `get_current_user` — pour tester le vrai
    parcours JWT de bout en bout (register -> login -> jeton -> accès
    protégé ; 401 sans jeton ; etc.), cf. `tests/test_auth_router.py`."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

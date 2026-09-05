from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Classe de base déclarative partagée par tous les modèles ORM."""

    pass


def get_db() -> Generator:
    """Dépendance FastAPI : fournit une session DB par requête et la ferme après usage."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

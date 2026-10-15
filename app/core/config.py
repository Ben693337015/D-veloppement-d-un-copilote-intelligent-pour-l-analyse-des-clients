from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration centralisée de l'application, chargée depuis .env.

    Cf. README.md §Architecture — cette classe est le seul point d'entrée
    pour lire une variable d'environnement ailleurs dans le code.
    """

    PROJECT_NAME: str = "Copilote IA PME"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"

    DATABASE_URL: str = (
        "postgresql+psycopg2://copilote_user:change_me@localhost:5432/copilote_pme"
    )

    SECRET_KEY: str = "change_me_super_secret"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Clé générique : le fournisseur est reconnu à son préfixe (sk-ant-,
    # sk-or-, gsk_, AIza/AQ., sk-) ; format inconnu -> Anthropic (cf.
    # llm_client.diagnostic).
    LLM_API_KEY: str | None = None
    # Fournisseur explicitement forcé ("anthropic"|"openai"|"groq"|"google"
    # |"openrouter") ; si absent ou sans clé correspondante configurée, le
    # premier fournisseur disponible est choisi automatiquement (cf.
    # app.services.llm_client.resolve_provider pour l'ordre de priorité).
    LLM_PROVIDER: str | None = None
    LLM_MODEL: str | None = None  # override du modèle par défaut du fournisseur choisi
    # URL de base de l'API du fournisseur, à la place de l'URL officielle
    # (ex. "http://localhost:11434/v1" pour Ollama ou LM Studio, un proxy
    # d'entreprise, ou un serveur simulé en test de bout en bout). Doit
    # inclure le préfixe de version ("/v1", "/v1beta") : le client ajoute
    # seulement "/chat/completions", "/messages" ou "/models/...".
    LLM_BASE_URL: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None

    # Accès du copilote à la base (texte -> SQL en lecture seule, cf.
    # app/services/sql_agent.py). Désactivable : le copilote se contente
    # alors de la synthèse d'indicateurs comme avant.
    COPILOTE_ACCES_BASE: bool = True
    # Devise des montants importés (ex. "FCFA", "€") : les fichiers PME n'en
    # indiquent pas. Vide = inconnue -> le copilote n'affiche aucun symbole
    # monétaire plutôt que d'en inventer un.
    DEVISE: str | None = None
    # Optionnel (recommandé en production) : connexion dédiée avec un rôle
    # PostgreSQL limité à SELECT, cf. scripts/init_db.sql (copilote_lecture).
    COPILOTE_DATABASE_URL_LECTURE: str | None = None
    # E-mails des clients lisibles par le copilote (désactivé par défaut :
    # donnée personnelle envoyée au fournisseur LLM externe). Les mots de
    # passe (table users) ne sont jamais accessibles.
    COPILOTE_ACCES_EMAILS: bool = False

    # Pré-calcul des prévisions des 3 modèles après chaque import de ventes
    # (cf. app/services/comparaison_previsions.py). Désactivé dans les tests.
    PREVISIONS_PRECALCUL: bool = True

    RFM_ANALYSIS_WINDOW_DAYS: int = 365
    RFM_WINSOR_PERCENTILE: float = 99.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

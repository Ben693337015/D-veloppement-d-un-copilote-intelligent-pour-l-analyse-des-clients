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

    # Compatibilité ascendante (ancien format .env, une seule clé générique,
    # traitée comme une clé Anthropic par défaut — cf. llm_client.py).
    LLM_API_KEY: str | None = None
    # Fournisseur explicitement forcé ("anthropic"|"openai"|"groq"|"google"
    # |"openrouter") ; si absent ou sans clé correspondante configurée, le
    # premier fournisseur disponible est choisi automatiquement (cf.
    # app.services.llm_client.resolve_provider pour l'ordre de priorité).
    LLM_PROVIDER: str | None = None
    LLM_MODEL: str | None = None  # override du modèle par défaut du fournisseur choisi
    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None

    RFM_ANALYSIS_WINDOW_DAYS: int = 365
    RFM_WINSOR_PERCENTILE: float = 99.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

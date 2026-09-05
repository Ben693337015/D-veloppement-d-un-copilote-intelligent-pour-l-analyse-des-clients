from __future__ import annotations

from pydantic import BaseModel, Field

# Identifiants de section, dans l'ordre d'apparition possible dans le PDF.
# Reprend les cases à cocher de l'écran "Génération du Rapport PDF" fourni
# en exemple (Page de garde, KPIs globaux, Coude & GridSearch, Répartition
# & CA personas, Distributions RFM, Fiches personas & recos, Extrait
# tableau complet...), adaptées aux données réellement disponibles côté API.
SECTIONS_DISPONIBLES: dict[str, str] = {
    "page_garde": "Page de garde",
    "kpis_globaux": "KPIs globaux",
    "coude_silhouette": "Coude & silhouette K-Means",
    "distribution_segments": "Distribution des segments RFM",
    "segments_summary": "Répartition & recommandations par segment",
    "top_clients": "Extrait des meilleurs clients",
}

SECTIONS_PAR_DEFAUT = list(SECTIONS_DISPONIBLES.keys())


class RapportPDFRequest(BaseModel):
    entreprise: str = Field(default="Mon Entreprise", max_length=200)
    auteur: str = Field(default="Équipe", max_length=200)
    theme_sombre: bool = False
    sections: list[str] = Field(default_factory=lambda: list(SECTIONS_PAR_DEFAUT))
    top_n_clients: int = Field(default=20, ge=1, le=200)

    def sections_valides(self) -> list[str]:
        """Filtre silencieusement les identifiants de section inconnus plutôt
        que d'échouer — un futur Dashboard qui envoie une section pas encore
        supportée ne doit pas casser toute la génération du rapport."""
        return [s for s in self.sections if s in SECTIONS_DISPONIBLES]

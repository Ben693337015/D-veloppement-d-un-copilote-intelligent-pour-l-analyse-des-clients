from __future__ import annotations

from pydantic import BaseModel, Field

# Identifiants de section, dans l'ordre d'apparition possible dans le PDF.
# Reprend les cases à cocher de l'écran "Génération du Rapport PDF" fourni
# en exemple (Page de garde, KPIs globaux, Coude & GridSearch, Répartition
# & CA personas, Distributions RFM, Fiches personas & recos, Extrait
# tableau complet...), adaptées aux données réellement disponibles côté API.
SECTIONS_DISPONIBLES: dict[str, str] = {
    "page_garde": "Page de garde",
    "resume": "Résumé exécutif",
    "kpis_globaux": "Indicateurs clés",
    "segmentation": "Segmentation — comparaison des 3 algorithmes",
    "segments": "Segments et actions recommandées",
    "clients": "Clients — concentration et meilleurs clients",
    "previsions_ventes": "Prévision des ventes — 3 modèles",
    "tresorerie": "Trésorerie — solde et prévision",
    "stocks": "Stocks — alertes de réapprovisionnement",
}

# Anciens identifiants (avant la refonte du 13/10/2026), acceptés pour ne pas
# casser un appel existant : ils pointent vers la section qui les remplace.
ALIAS_SECTIONS: dict[str, str] = {
    "coude_silhouette": "segmentation",
    "distribution_segments": "segments",
    "segments_summary": "segments",
    "top_clients": "clients",
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
        valides: list[str] = []
        for s in self.sections:
            s = ALIAS_SECTIONS.get(s, s)
            if s in SECTIONS_DISPONIBLES and s not in valides:
                valides.append(s)
        # Toujours dans l'ordre du rapport, quel que soit l'ordre des cases cochées.
        return [s for s in SECTIONS_DISPONIBLES if s in valides]

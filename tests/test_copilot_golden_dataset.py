"""Tâche #10 de la roadmap de clôture — verrouille le comportement de
l'agent (tâches #6/#7/#8) dans la suite de tests, en s'appuyant sur le
golden dataset (tâche #9, `golden_dataset_copilot.json`).

Ces tests valident le MÉCANISME d'exécution (si le LLM demande l'outil X,
celui-ci s'exécute correctement sur de vraies données et produit une
réponse) — pas la capacité du vrai LLM à choisir le bon outil pour une
question donnée, qui relève d'une évaluation manuelle contre une clé API
réelle (hors CI, cf. roadmap tâche #9 : "sans RAGAS, disproportionné pour
ce volume"). Le LLM est systématiquement mocké, comme dans
`test_llm_client.py` et `test_copilot_router.py`.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from app.models import StockProduit, Transaction, Tresorerie
from app.services import copilot_service, llm_client

GOLDEN = json.loads((Path(__file__).parent / "golden_dataset_copilot.json").read_text(encoding="utf-8"))["cas"]
_CAS_AVEC_OUTIL = [c for c in GOLDEN if c["outil_attendu"]]
_CAS_SANS_OUTIL = [c for c in GOLDEN if not c["outil_attendu"]]


class _ReponseFactice:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _mock_llm_appelle_outil(outil: str, parametres: dict):
    """Simule un LLM qui demande directement l'outil attendu du golden
    dataset, puis conclut au tour suivant — teste que l'exécution réelle
    de l'outil (vraies données en base) ne casse rien, une fois le choix
    d'outil supposé correct."""
    appels = {"n": 0}

    def _post(url, headers, json, timeout):
        appels["n"] += 1
        if appels["n"] == 1:
            return _ReponseFactice(
                {
                    "stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "t1", "name": outil, "input": parametres}],
                }
            )
        return _ReponseFactice(
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Réponse finale factice."}]}
        )

    return _post


def _mock_llm_reponse_directe(url=None, headers=None, json=None, timeout=None):
    return _ReponseFactice(
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Réponse directe sans outil."}]}
    )


def _seed_donnees_minimales(db_session):
    """Un peu de données dans chaque table pour que les 5 outils aient
    quelque chose de réel à retourner (évite un historique 'insuffisant'
    qui masquerait un vrai bug d'exécution derrière un simple repli)."""
    debut = date(2024, 1, 1)
    for i in range(20):
        jour = debut + timedelta(days=i)
        db_session.add(
            Transaction(
                client_id=None,
                numero_facture=f"INV-{i:04d}",
                code_produit="P1",
                quantite=1,
                prix_unitaire=50.0,
                date_transaction=jour,
                est_retour=False,
            )
        )
        db_session.add(
            Tresorerie(date_mouvement=jour, type_mouvement="encaissement", categorie="ventes", montant=50.0)
        )
    db_session.add(StockProduit(code_produit="P1", nom_produit="Produit test", quantite_disponible=2, seuil_alerte=10))
    db_session.commit()


def _sans_cle_llm(monkeypatch):
    for nom in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "LLM_API_KEY",
    ):
        monkeypatch.setattr(llm_client.settings, nom, None, raising=False)


def test_golden_dataset_est_bien_forme():
    assert len(GOLDEN) >= 10
    assert len(_CAS_AVEC_OUTIL) >= 5  # au moins un par outil
    assert len(_CAS_SANS_OUTIL) >= 2
    outils_couverts = {c["outil_attendu"] for c in _CAS_AVEC_OUTIL}
    assert outils_couverts == {
        "get_kpis",
        "get_sales_forecast",
        "get_cashflow_forecast",
        "get_rfm_segments",
        "get_stock_alerts",
    }


@pytest.mark.parametrize("cas", _CAS_AVEC_OUTIL, ids=[c["id"] for c in _CAS_AVEC_OUTIL])
def test_golden_dataset_outil_s_execute_sans_erreur(db_session, monkeypatch, cas):
    """Pour chaque question du golden dataset attendant un outil : si le
    LLM le demande, l'outil s'exécute réellement (vraies données en base)
    et la réponse finale arrive jusqu'à l'utilisateur, sans plantage."""
    _seed_donnees_minimales(db_session)
    monkeypatch.setattr(llm_client.settings, "ANTHROPIC_API_KEY", "sk-ant-test", raising=False)
    monkeypatch.setattr(llm_client.settings, "LLM_PROVIDER", None, raising=False)
    monkeypatch.setattr(
        httpx, "post", _mock_llm_appelle_outil(cas["outil_attendu"], cas["parametres_attendus"] or {})
    )

    resultat = copilot_service.answer_question(db_session, cas["question"])
    assert resultat["reponse"] == "Réponse finale factice."
    assert f"outil:{cas['outil_attendu']}" in resultat["sources"]


@pytest.mark.parametrize("cas", _CAS_SANS_OUTIL, ids=[c["id"] for c in _CAS_SANS_OUTIL])
def test_golden_dataset_sans_outil_ne_force_pas_appel(db_session, monkeypatch, cas):
    """Questions générales du golden dataset : si le LLM répond
    directement (comme un LLM réel le ferait ici), aucun outil superflu
    n'est ajouté aux sources — le mécanisme ne force rien."""
    monkeypatch.setattr(llm_client.settings, "ANTHROPIC_API_KEY", "sk-ant-test", raising=False)
    monkeypatch.setattr(llm_client.settings, "LLM_PROVIDER", None, raising=False)
    monkeypatch.setattr(httpx, "post", _mock_llm_reponse_directe)

    resultat = copilot_service.answer_question(db_session, cas["question"])
    assert resultat["reponse"] == "Réponse directe sans outil."
    assert not any(s.startswith("outil:") for s in resultat["sources"])


def test_fallback_local_fonctionne_sans_cle_api_sur_echantillon(db_session, monkeypatch):
    """'fallback local toujours fonctionnel sans clé API' (roadmap #10) —
    vérifié sur un échantillon du golden dataset, aucun fournisseur
    configuré : ne doit jamais planter ni tenter d'appel réseau."""
    _sans_cle_llm(monkeypatch)
    for cas in GOLDEN[:5]:
        resultat = copilot_service.answer_question(db_session, cas["question"])
        assert resultat["reponse"].startswith("Mode local")


def test_parametre_invalide_gere_sans_plantage(db_session, monkeypatch):
    """'gestion d'un paramètre invalide' (roadmap #10) — un LLM qui envoie
    un paramètre hors bornes (cf. tâche #8) ne fait jamais planter
    l'endpoint ; l'erreur est transmise AU LLM en texte clair, qui peut
    conclure normalement."""
    monkeypatch.setattr(llm_client.settings, "ANTHROPIC_API_KEY", "sk-ant-test", raising=False)
    monkeypatch.setattr(llm_client.settings, "LLM_PROVIDER", None, raising=False)
    monkeypatch.setattr(
        httpx, "post", _mock_llm_appelle_outil("get_sales_forecast", {"horizon_jours": 99999, "modele": "prophet"})
    )
    resultat = copilot_service.answer_question(db_session, "prévision sur 99999 jours")
    assert resultat["reponse"] == "Réponse finale factice."
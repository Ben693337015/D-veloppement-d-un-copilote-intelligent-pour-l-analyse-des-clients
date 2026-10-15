"""Outils de calcul du copilote (copilote_outils) et orchestrateur SQL + outils."""

from datetime import date, datetime, timedelta

import pytest

from app.core.config import settings
from app.models import StockProduit, Transaction, Tresorerie
from app.services import copilote_outils as co
from app.services import llm_client


def _ventes(db, code, quantite_par_jour, jours=30, fin=datetime(2024, 6, 30)):
    for j in range(jours):
        db.add(Transaction(numero_facture=f"{code}-{j}", code_produit=code, quantite=quantite_par_jour,
                           prix_unitaire=100, date_transaction=fin - timedelta(days=j)))


def _stock(db, code, quantite, delai=7, cout=None):
    db.add(StockProduit(code_produit=code, nom_produit=f"Produit {code}", quantite_disponible=quantite,
                        delai_livraison_jours=delai, cout_unitaire=cout))


# ── Lecture des appels écrits par le LLM ────────────────────────────────────


def test_extraire_appels_lit_les_blocs_outil_et_ignore_le_reste():
    plan = (
        "```sql\nSELECT 1\n```\n"
        '```outil\n{"nom": "prevision_ventes", "parametres": {"horizon_jours": 60}}\n```\n'
        "```outil\npas du json\n```"
    )
    appels, erreurs = co.extraire_appels(plan)
    assert [(a.nom, a.parametres) for a in appels] == [("prevision_ventes", {"horizon_jours": 60})]
    assert len(erreurs) == 1 and "illisible" in erreurs[0]


def test_extraire_appels_plafonne_le_nombre_d_outils():
    bloc = '```outil\n{"nom": "tresorerie_prevue"}\n```\n'
    appels, _ = co.extraire_appels(bloc * 5)
    assert len(appels) == co.APPELS_MAX


def test_executer_refuse_outil_inconnu_et_parametres_hors_bornes(db_session):
    inconnu = co.executer(db_session, co.AppelOutil("supprimer_tout", {}))
    assert "outil inconnu" in inconnu.erreur and inconnu.resultat is None
    hors_bornes = co.executer(db_session, co.AppelOutil("prevision_ventes", {"horizon_jours": 500}))
    assert "horizon_jours" in hors_bornes.erreur
    sans_variation = co.executer(db_session, co.AppelOutil("simulation_ventes", {}))
    assert "variation_pct" in sans_variation.erreur


# ── Réapprovisionnement ─────────────────────────────────────────────────────


def test_reapprovisionnement_calcule_urgence_et_quantite(db_session):
    _ventes(db_session, "A", 10)  # 10 unités/jour
    _stock(db_session, "A", 50, delai=7, cout=2.5)  # 5 jours de stock < 7 jours de délai -> urgent
    _ventes(db_session, "B", 2)  # 2 unités/jour
    _stock(db_session, "B", 100, delai=7)  # 50 jours de stock -> suffisant pour 7 + 30 jours
    _stock(db_session, "C", 5)  # jamais vendu
    db_session.commit()

    r = co.reapprovisionnement(db_session, co.ParamsReapprovisionnement(jours_couverture=30))
    assert r["produits_urgents"] == 1 and r["produits_a_commander"] == 1
    a = r["produits"][0]
    assert a["code_produit"] == "A" and a["urgence"] == "urgent"
    assert a["ventes_par_jour"] == 10 and a["jours_de_stock_restants"] == 5
    assert a["quantite_a_commander"] == 10 * (7 + 30) - 50  # 320
    assert a["cout_estime"] == 800
    assert r["cout_total_estime"] == 800


def test_reapprovisionnement_un_produit_et_stock_vide(db_session):
    assert "Aucun stock" in co.reapprovisionnement(db_session, co.ParamsReapprovisionnement())["message"]
    _stock(db_session, "B", 100)
    db_session.commit()
    r = co.reapprovisionnement(db_session, co.ParamsReapprovisionnement(code_produit="B"))
    assert [p["code_produit"] for p in r["produits"]] == ["B"]
    assert r["cout_total_estime"] is None  # aucun coût connu : pas de « 0 » trompeur
    assert r["produits"][0]["urgence"] == "suffisant"


# ── Trésorerie et simulation ────────────────────────────────────────────────


def _mouvements(db, jours=40):
    for j in range(jours):
        jour = date(2024, 5, 1) + timedelta(days=j)
        db.add(Tresorerie(date_mouvement=jour, type_mouvement="encaissement", montant=1000))
        db.add(Tresorerie(date_mouvement=jour, type_mouvement="decaissement", montant=900))
    db.commit()


def test_tresorerie_prevue_renvoie_solde_et_alerte(db_session):
    _mouvements(db_session)
    r = co.tresorerie_prevue(db_session, co.ParamsTresorerie(horizon_jours=14, seuil_critique=1_000_000))
    assert r["solde_actuel"] == pytest.approx(4000)
    assert r["niveau_alerte"] == "risque_deficit"  # solde ~4 000 très en dessous du seuil demandé
    assert "sous votre seuil critique de 1000000" in r["message"]  # positif : pas « négatif »
    assert set(r) >= {"solde_prevu_fin_periode", "point_le_plus_bas", "date_du_point_le_plus_bas", "message"}


def test_simulation_ajoute_l_ecart_de_ventes_a_la_tresorerie(db_session, monkeypatch):
    _mouvements(db_session)
    monkeypatch.setattr(co, "prevision_ventes", lambda db, p: {"disponible": True, "total_prevu": 10_000.0})
    r = co.simulation_ventes(db_session, co.ParamsSimulation(variation_pct=-20, horizon_jours=14))
    assert r["ventes_dans_ce_scenario"] == 8000 and r["ecart_de_ventes"] == -2000
    fin_base = r["tresorerie_sans_changement"]["solde_prevu_fin_periode"]
    fin_scenario = r["tresorerie_dans_ce_scenario"]["solde_prevu_fin_periode"]
    assert fin_scenario == pytest.approx(fin_base - 2000)
    assert "hypothese" in r


def test_prevision_ventes_auto_reprend_la_lecture_dirigeant(db_session, monkeypatch):
    points = [{"date_prevision": date(2024, 7, j), "valeur_prevue": 10.0} for j in range(1, 15)]
    monkeypatch.setattr(co.comparaison_previsions, "comparer_modeles", lambda db, h: {
        "lecture_dirigeant": {"disponible": True, "total_prevu": 140.0, "modele_retenu": "arima"},
        "modeles": [{"modele": "arima", "points": points}],
    })
    r = co.prevision_ventes(db_session, co.ParamsPrevisionVentes(horizon_jours=14))
    assert r["total_prevu"] == 140 and "modele_retenu" not in r  # pas de détail technique
    assert [s["ventes_prevues"] for s in r["detail_par_semaine"]] == [70, 70]


# ── Orchestrateur : le copilote choisit et exécute les outils ──────────────


class FauxLLM:
    def __init__(self, *reponses):
        self.reponses, self.appels = list(reponses), []

    def __call__(self, system_prompt, message):
        self.appels.append((system_prompt, message))
        return self.reponses.pop(0)


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "COPILOTE_ACCES_BASE", True)

    def installer(*reponses):
        faux = FauxLLM(*reponses)
        monkeypatch.setattr(llm_client, "generate_completion", faux)
        return faux

    return installer


def test_le_copilote_execute_un_outil_et_repond_avec_le_resultat(client, db_session, llm):
    _ventes(db_session, "A", 10)
    _stock(db_session, "A", 50)
    db_session.commit()
    faux = llm(
        '```outil\n{"nom": "reapprovisionnement", "parametres": {"jours_couverture": 30}}\n```',
        "Commandez 320 unités du produit A.",
    )
    corps = client.post("/api/v1/copilot/chat", json={"question": "Que dois-je commander ?"}).json()

    assert corps["reponse"] == "Commandez 320 unités du produit A."
    assert corps["outils_utilises"] == [{
        "nom": "reapprovisionnement", "libelle": "Recommandation de réapprovisionnement",
        "parametres": {"code_produit": None, "jours_couverture": 30}, "statut": "ok", "erreur": None,
    }]
    assert "outil:reapprovisionnement" in corps["sources"]
    assert corps["requetes_sql"] == []
    prompt_planification, prompt_reponse = faux.appels[0][0], faux.appels[1][1]
    assert "reapprovisionnement" in prompt_planification and "```outil```" in prompt_planification
    assert '"quantite_a_commander": 320' in prompt_reponse


def test_le_copilote_combine_sql_et_outil(client, db_session, llm):
    _ventes(db_session, "A", 10)
    _stock(db_session, "A", 50)
    db_session.commit()
    llm(
        "```sql\nSELECT code_produit, SUM(quantite) AS qte FROM transactions GROUP BY code_produit\n```\n"
        '```outil\n{"nom": "reapprovisionnement", "parametres": {}}\n```',
        "Vous avez vendu 300 unités de A ; commandez-en 320.",
    )
    corps = client.post("/api/v1/copilot/chat", json={"question": "Ventes et stock de A ?"}).json()
    assert len(corps["requetes_sql"]) == 1 and corps["lignes_base"] == 1
    assert [o["nom"] for o in corps["outils_utilises"]] == ["reapprovisionnement"]


def test_un_outil_en_erreur_est_signale_sans_casser_la_reponse(client, db_session, llm):
    faux = llm('```outil\n{"nom": "prevision_ventes", "parametres": {"horizon_jours": 999}}\n```', "Désolé.")
    corps = client.post("/api/v1/copilot/chat", json={"question": "Ventes dans 3 ans ?"}).json()
    assert corps["mode"] == "llm" and corps["outils_utilises"][0]["statut"] == "erreur"
    assert "ÉCHEC" in faux.appels[1][1] and "horizon_jours" in faux.appels[1][1]

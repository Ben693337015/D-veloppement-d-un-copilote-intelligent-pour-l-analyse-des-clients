"""Service métier — assistant conversationnel RAG mutualisé (Module D, Cadrage §CDCF 3.4).

Contexte injecté dans le prompt : synthèse texte des indicateurs réels
(KPIs, alertes stock/trésorerie, segments RFM, prévisions ventes/trésorerie,
échantillon de clients par segment) générée par `build_context_snapshot` —
une forme légère de RAG "context-injection" plutôt qu'un vrai index
vectoriel ChromaDB (non présent dans ce projet malgré une mention dans la
vision initiale — cf. requirements.txt : pas de dépendance chromadb).

`build_context_snapshot` vise l'EXHAUSTIVITÉ des analyses déjà calculées par
les autres services (pas seulement quelques KPIs) : c'est le contrat qui
permet au copilote de répondre correctement à une question sur n'importe
quel module (ventes, trésorerie, stocks, clients) sans halluciner un chiffre
absent du contexte. Ce qui reste volontairement HORS contexte : le détail
ligne à ligne des transactions (potentiellement des dizaines de milliers de
lignes, cf. docstring historique) — un index vectoriel deviendrait
nécessaire pour exposer CE niveau de détail au LLM ; les AGRÉGATS et
analyses (RFM, prévisions, alertes), eux, tiennent largement dans la fenêtre
du modèle quelle que soit la taille de la base sous-jacente, donc entrent
intégralement dans le contexte.

Le LLM est appelé via `app.services.llm_client` (multi-fournisseur :
Anthropic, OpenAI, Groq, Google, OpenRouter — cf. ce module pour l'ordre de
priorité). Sans clé configurée, retombe sur le mode local (résumé factuel).
"""

import json

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.schemas.agent_tools import (
    GetCashflowForecastParams,
    GetKpisParams,
    GetRfmSegmentsParams,
    GetSalesForecastParams,
    GetStockAlertsParams,
)
from app.schemas.client import ClientRFM
from app.services import analytics_service, forecasting_service, llm_client, marketing_service

SYSTEM_PROMPT = (
    "Tu es le copilote IA d'une plateforme d'analyse pour PME (projet académique "
    "Groupe 2, Master IA Appliquée, Université de Ngaoundéré). Réponds en français, "
    "de façon concise et factuelle, en t'appuyant STRICTEMENT sur le contexte fourni "
    "ci-dessous (indicateurs, prévisions et segments réels de l'entreprise, "
    "potentiellement TRONQUÉS pour les listes longues — dis-le si la question porte "
    "sur un élément qui pourrait être hors de l'échantillon montré). N'invente jamais "
    "un chiffre absent du contexte ; si l'information demandée n'y figure pas, dis-le "
    "clairement plutôt que de deviner. Le contexte peut inclure un segment "
    "\"Atypique (B2B/grossiste)\" (clients isolés par DBSCAN, à traiter hors "
    "campagnes marketing grand public)."
)

# Tâche #6/#7 — system prompt dédié au mode tool-calling (contexte NON injecté
# d'avance : l'agent interroge lui-même les outils dont il a besoin, plutôt
# que de recevoir un résumé pré-mâché comme en mode chat simple ci-dessus).
SYSTEM_PROMPT_OUTILS = (
    "Tu es le copilote IA d'une plateforme d'analyse pour PME (projet académique "
    "Groupe 2, Master IA Appliquée, Université de Ngaoundéré). Réponds en français, "
    "de façon concise et factuelle. Tu as accès à des outils pour consulter les "
    "indicateurs réels de l'entreprise (ventes, trésorerie, stocks, segments clients) "
    "— UTILISE-LES pour toute question portant sur des chiffres ou des données de "
    "l'entreprise plutôt que d'inventer une réponse. N'appelle PAS d'outil pour une "
    "question générale qui n'en a pas besoin (salutation, question sur ton "
    "fonctionnement, etc.). Si l'utilisateur mentionne un \"souci\" ou un "
    "\"problème\" sans préciser lequel, comprends-le par défaut comme une "
    "question sur l'activité de son ENTREPRISE (ventes, trésorerie, stocks, "
    "clients) — pas sur ton propre fonctionnement technique — sauf s'il est "
    "explicitement question de toi ou de l'application."
)


# Tâche #4 (AGENT_TOOLS.md) traduite au format Anthropic natif — un outil par
# endpoint backend déjà livré et testé en Semaine 1, aucune nouvelle logique
# métier (cf. `_executer_outil` : simples appels aux services existants).
TOOLS_ANTHROPIC = [
    {
        "name": "get_kpis",
        "description": "KPIs globaux de la PME : nombre de clients, transactions, CA total, stock total, solde de trésorerie estimé.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_sales_forecast",
        "description": "Prévision des ventes sur un horizon donné (1-90 jours). Renvoie aussi les variables les plus influentes si modele=xgboost.",
        "input_schema": {
            "type": "object",
            "properties": {
                "horizon_jours": {"type": "integer", "minimum": 1, "maximum": 90, "default": 30},
                "modele": {"type": "string", "enum": ["prophet", "arima", "xgboost"], "default": "prophet"},
            },
        },
    },
    {
        "name": "get_cashflow_forecast",
        "description": "Prévision de trésorerie + alerte de risque de déficit (ok/vigilance/risque_deficit) sur un horizon donné.",
        "input_schema": {
            "type": "object",
            "properties": {
                "horizon_jours": {"type": "integer", "minimum": 1, "maximum": 90, "default": 45},
                "modele": {"type": "string", "enum": ["prophet", "arima", "xgboost"], "default": "prophet"},
                "seuil_critique": {"type": "number", "default": 0},
            },
        },
    },
    {
        "name": "get_rfm_segments",
        "description": "Liste des clients avec leur segment RFM (ex. 'Champions', 'À risque', 'Perdus') et leurs scores R/F/M.",
        "input_schema": {
            "type": "object",
            "properties": {
                "segment": {"type": "string", "description": "Filtrer sur un segment précis, ex. 'À risque'."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
            },
        },
    },
    {
        "name": "get_stock_alerts",
        "description": "Alertes de réapprovisionnement (produits en stock critique ou à surveiller).",
        "input_schema": {"type": "object", "properties": {}},
    },
]

MAX_TOURS_OUTILS = 5  # garde-fou anti-boucle si le LLM s'entête à rappeler des outils (tâche #8)

def _condenser_prevision(prevision: dict, max_points: int = 8) -> dict:
    """Réduit la taille d'une prévision (ventes ou trésorerie) avant de
    l'envoyer au LLM : au-delà de `max_points` jours, ne garder que les
    premiers/derniers points + des statistiques agrégées sur la série
    complète, plutôt que tous les points bruts. Réduit sensiblement la
    consommation de tokens (le quota gratuit Groq est limité en tokens/
    minute) sans perdre l'information utile pour répondre à l'utilisateur.
    N'affecte QUE ce qui est envoyé au LLM — l'endpoint REST direct
    (`GET /analytics/forecast/sales`) continue de renvoyer tous les points."""
    points = prevision.get("points", [])
    if len(points) <= max_points:
        return prevision

    valeurs = [p["valeur_prevue"] for p in points]
    i_min, i_max = valeurs.index(min(valeurs)), valeurs.index(max(valeurs))
    n_premiers = max_points // 2
    n_derniers = max_points - n_premiers

    resume = dict(prevision)
    resume["points"] = points[:n_premiers] + points[-n_derniers:]
    resume["points_omis_pour_concision"] = len(points) - max_points
    resume["statistiques_serie_complete"] = {
        "valeur_min": valeurs[i_min],
        "date_min": points[i_min]["date_prevision"],
        "valeur_max": valeurs[i_max],
        "date_max": points[i_max]["date_prevision"],
        "valeur_moyenne": sum(valeurs) / len(valeurs),
    }
    return resume


def _condenser_liste(nom_cle_echantillon: str, elements: list, max_elements: int = 15) -> list | dict:
    """Même principe que `_condenser_prevision`, pour une liste d'objets
    (clients RFM, alertes stock) potentiellement longue."""
    if len(elements) <= max_elements:
        return elements
    return {
        nom_cle_echantillon: elements[:max_elements],
        "n_total": len(elements),
        "elements_omis_pour_concision": len(elements) - max_elements,
    }

_MODELES_PARAMETRES_OUTILS = {
    "get_kpis": GetKpisParams,
    "get_sales_forecast": GetSalesForecastParams,
    "get_cashflow_forecast": GetCashflowForecastParams,
    "get_rfm_segments": GetRfmSegmentsParams,
    "get_stock_alerts": GetStockAlertsParams,
}


def _executer_outil(nom: str, params: dict, db: Session):
    """Tâche #7 — exécute réellement l'outil demandé par le LLM en appelant
    le service métier correspondant (déjà écrit et testé en Semaine 1) :
    AUCUNE nouvelle logique métier ici, uniquement l'exposition en outil.

    Tâche #8 — les paramètres reçus sont d'abord validés (bornes, type,
    valeurs autorisées) via les modèles Pydantic de `app.schemas.agent_tools`
    AVANT tout appel au service métier : un LLM peut envoyer n'importe
    quoi (horizon démesuré, type inattendu), contrairement à un appel
    direct à l'endpoint REST déjà protégé par FastAPI/Query.

    Lève ValueError sur nom d'outil inconnu OU paramètres invalides —
    capturé par l'appelant (`_repondre_avec_outils`), jamais de plantage,
    l'erreur est renvoyée en texte clair au LLM plutôt qu'à l'utilisateur
    brut."""
    modele_parametres = _MODELES_PARAMETRES_OUTILS.get(nom)
    if modele_parametres is None:
        raise ValueError(f"outil inconnu : '{nom}'")
    try:
        p = modele_parametres.model_validate(params)
    except ValidationError as exc:
        premiere_erreur = exc.errors()[0]
        champ = ".".join(str(x) for x in premiere_erreur["loc"]) or "(paramètre)"
        raise ValueError(f"paramètre invalide pour '{nom}' — {champ} : {premiere_erreur['msg']}") from exc

    if nom == "get_kpis":
        return analytics_service.get_kpis_globaux(db)
    if nom == "get_sales_forecast":
        resultat = forecasting_service.get_previsions_ventes(db, horizon_jours=p.horizon_jours, modele=p.modele)
        return _condenser_prevision(resultat)
    if nom == "get_cashflow_forecast":
        prevision = forecasting_service.get_previsions_tresorerie(db, p.horizon_jours, p.modele)
        alerte = analytics_service.get_alerte_tresorerie(prevision, p.horizon_jours, p.seuil_critique)
        return {"prevision": _condenser_prevision(prevision), "alerte": alerte}
    if nom == "get_rfm_segments":
        clients = marketing_service.list_clients_rfm(db, segment=p.segment, limit=p.limit)
        clients_json = [ClientRFM.model_validate(c).model_dump(mode="json") for c in clients]
        return _condenser_liste("clients_echantillon", clients_json)
    if nom == "get_stock_alerts":
        return _condenser_liste("alertes_echantillon", analytics_service.get_stock_alertes(db))
    raise ValueError(f"outil inconnu : '{nom}'")  # inatteignable (couvert par le dict ci-dessus), filet de sécurité

def _repondre_avec_outils(db: Session, fournisseur: str, question: str) -> dict:
    """Tâche #7 — boucle d'exécution complète : demande d'outil du LLM ->
    appel du service métier réel -> second appel LLM avec le résultat ->
    réponse finale. Volontairement UN SEUL appel avec boucle d'outil,
    pas de framework planner/executor séparé (cf. roadmap tâche #7)."""
    messages: list[dict] = [{"role": "user", "content": question}]
    outils_utilises: list[str] = []
    dernier_resultat: dict | None = None

    for _ in range(MAX_TOURS_OUTILS):
        dernier_resultat = llm_client.generate_completion_with_tools(
            SYSTEM_PROMPT_OUTILS, messages, TOOLS_ANTHROPIC
        )

        if dernier_resultat["stop_reason"] != "tool_use" or not dernier_resultat["tool_calls"]:
            return {
                "reponse": dernier_resultat["text"] or "(réponse vide)",
                "sources": ["analytics_service", "marketing_service", f"llm:{fournisseur}", *outils_utilises],
                "explicabilite": [],
            }

        messages.append({"role": "assistant", "content": dernier_resultat["assistant_content"]})

        blocs_resultats = []
        for appel in dernier_resultat["tool_calls"]:
            outils_utilises.append(f"outil:{appel['name']}")
            try:
                sortie = _executer_outil(appel["name"], appel["input"], db)
                contenu_resultat = json.dumps(sortie, default=str, ensure_ascii=False)
            except (ValueError, TypeError) as exc:
                # Tâche #8 : jamais de plantage sur outil/paramètre invalide —
                # l'erreur est renvoyée AU LLM (pas à l'utilisateur), qui peut
                # reformuler son appel ou l'expliquer dans sa réponse finale.
                contenu_resultat = json.dumps({"erreur": str(exc)}, ensure_ascii=False)
            blocs_resultats.append(
                {"type": "tool_result", "tool_use_id": appel["id"], "content": contenu_resultat}
            )
        messages.append({"role": "user", "content": blocs_resultats})

    return {
        "reponse": (
            "Je n'ai pas pu obtenir de réponse définitive après plusieurs consultations "
            "d'outils — voici ce que j'ai trouvé jusqu'ici : "
            + (dernier_resultat["text"] or "aucune synthèse disponible.")
        ),
        "sources": ["analytics_service", "marketing_service", f"llm:{fournisseur}", *outils_utilises],
        "explicabilite": [],
    }


# Nombre de clients cités nommément par segment, pour illustrer une réponse
# concrète ("qui sont mes clients à risque ?") sans y dumper toute la table
# `clients` (qui peut compter des milliers de lignes sur une vraie PME).
_N_CLIENTS_EXEMPLE_PAR_SEGMENT = 5
_HORIZON_PREVISION_VENTES_CONTEXTE = 14
_HORIZON_PREVISION_TRESORERIE_CONTEXTE = 30


def _section_stock(db: Session) -> list[str]:
    alertes = analytics_service.get_stock_alertes(db)
    lignes = [f"Alertes stock actives: {len(alertes)}"]
    for a in alertes:
        lignes.append(
            f"  - {a['nom_produit']} ({a['code_produit']}) : {a['quantite_disponible']} unité(s) "
            f"disponible(s), seuil critique {a['seuil_alerte']} — niveau {a['niveau']}"
        )
    return lignes


def _section_segments(db: Session) -> list[str]:
    segments = marketing_service.get_segment_summary(db)
    if not segments:
        return ["Segmentation RFM : aucune (pipeline jamais exécuté ou aucun client importé)."]
    derniere_maj = marketing_service.get_derniere_maj_segmentation(db)
    lignes = [
        f"Segmentation RFM (dernière mise à jour : "
        f"{derniere_maj.strftime('%Y-%m-%d %H:%M') if derniere_maj else 'inconnue'}) :"
    ]
    for seg in segments:
        lignes.append(
            f"Segment {seg['segment_rfm']}: {seg['nb_clients']} clients, montant moyen "
            f"{seg['montant_total_moyen']:.2f}, churn moyen {seg['score_churn_moyen']}, "
            f"action recommandée : {seg['action_marketing_recommandee']}"
        )
        exemples = marketing_service.list_clients_rfm(
            db, segment=seg["segment_rfm"], limit=_N_CLIENTS_EXEMPLE_PAR_SEGMENT
        )
        if exemples:
            noms = ", ".join(
                f"{c.nom or c.code_client_externe or c.client_id} "
                f"(montant={float(c.montant_total or 0):.0f}, récence={c.recence_jours}j)"
                for c in exemples
            )
            suffixe = "..." if seg["nb_clients"] > len(exemples) else ""
            lignes.append(f"  - Exemples ({len(exemples)}/{seg['nb_clients']}) : {noms}{suffixe}")
    return lignes


# Modèle utilisé pour les DEUX prévisions injectées dans le contexte du
# copilote : ARIMA plutôt que Prophet (défaut de la plateforme sur
# `GET /analytics/forecast/sales`) — le chat doit rester réactif, or
# l'ajustement CmdStan/Prophet ajoute 1 à quelques secondes par appel,
# deux fois (validation + modèle final, cf. forecasting_service), à chaque
# message envoyé au copilote. ARIMA reste un modèle réellement validé
# (RMSE/MAE calculés de la même façon, cf. README §6), pas un repli
# dégradé — seul le choix de rapidité change, pas la rigueur.
_MODELE_PREVISION_CONTEXTE = "arima"


def _section_prevision_ventes(db: Session) -> list[str]:
    try:
        prevision = forecasting_service.get_previsions_ventes(
            db, horizon_jours=_HORIZON_PREVISION_VENTES_CONTEXTE, modele=_MODELE_PREVISION_CONTEXTE
        )
    except Exception as exc:  # jamais casser le contexte pour une prévision en échec
        return [f"Prévision des ventes : indisponible ({exc})."]
    points = prevision["points"]
    lignes = [
        f"Prévision des ventes à {prevision['horizon_jours']}j (modèle : {prevision['modele_utilise']}"
        + (
            f", RMSE={prevision['rmse_validation']:.2f}, MAE={prevision['mae_validation']:.2f}"
            if prevision["rmse_validation"] is not None
            else ""
        )
        + ") :"
    ]
    if points:
        lignes.append(
            f"  - Prochain jour ({points[0]['date_prevision']}) : {points[0]['valeur_prevue']:.2f}"
        )
        lignes.append(
            f"  - Dernier jour de l'horizon ({points[-1]['date_prevision']}) : "
            f"{points[-1]['valeur_prevue']:.2f}"
        )
    for avert in prevision["avertissements"]:
        lignes.append(f"  - Avertissement : {avert}")
    return lignes


def _section_tresorerie(db: Session) -> list[str]:
    try:
        prevision = forecasting_service.get_previsions_tresorerie(
            db, horizon_jours=_HORIZON_PREVISION_TRESORERIE_CONTEXTE, modele=_MODELE_PREVISION_CONTEXTE
        )
        alerte = analytics_service.get_alerte_tresorerie(
            prevision, _HORIZON_PREVISION_TRESORERIE_CONTEXTE
        )
    except Exception as exc:
        return [f"Prévision de trésorerie : indisponible ({exc})."]
    return [
        f"Prévision de trésorerie à {_HORIZON_PREVISION_TRESORERIE_CONTEXTE}j "
        f"(modèle : {prevision['modele_utilise']}) — niveau d'alerte : {alerte['niveau']}.",
        f"  - {alerte['message']}",
    ]


def build_context_snapshot(db: Session) -> str:
    """Construit un contexte texte à injecter dans le prompt RAG, en visant
    la couverture de TOUTES les analyses déjà calculées ailleurs dans
    l'application (KPIs, stocks, RFM + échantillon de clients, prévisions
    ventes et trésorerie) — pas seulement un résumé partiel — pour que le
    copilote puisse répondre à une question sur n'importe quel module sans
    halluciner une donnée absente."""
    kpis = analytics_service.get_kpis_globaux(db)

    lignes = [
        f"CA total: {kpis['chiffre_affaires_total']:.2f}",
        f"Clients: {kpis['nb_clients']} | Transactions: {kpis['nb_transactions']}",
        f"Stock total (unités): {kpis['stock_total_unites']}",
        f"Solde trésorerie estimé (encaissements - décaissements à date): {kpis['solde_tresorerie_estime']:.2f}",
        "",
        *_section_stock(db),
        "",
        *_section_segments(db),
        "",
        *_section_prevision_ventes(db),
        "",
        *_section_tresorerie(db),
    ]
    return "\n".join(lignes)


def answer_question(db: Session, question: str) -> dict:
    """Point d'entrée de /api/v1/copilot/chat.

    Sans fournisseur LLM configuré (cf. `llm_client.resolve_provider`),
    retourne un résumé factuel basé sur le contexte plutôt qu'une
    génération libre, pour que l'API reste utilisable en local sans
    dépendance externe.

    Si un fournisseur EST configuré, `llm_client.generate_completion` est
    appelé avec le contexte réel injecté dans le prompt système. Tout échec
    (réseau, authentification, quota, réponse inattendue) est intercepté
    (`llm_client.LLMError`) et bascule sur le même mode local — JAMAIS de
    500 brut, quelle que soit la qualité de la configuration LLM (bug déjà
    corrigé une première fois pour l'ancien stub, principe conservé pour
    l'intégration réelle : une clé mal configurée ne doit jamais casser
    l'endpoint pour l'utilisateur final)."""
    resolu = llm_client.resolve_provider()

    if resolu is None:
        contexte = build_context_snapshot(db)
        return {
            "reponse": (
                "Mode local (sans LLM configuré) — voici la synthèse des "
                f"indicateurs disponibles pour répondre à : « {question} »\n\n{contexte}"
            ),
            "sources": ["analytics_service", "marketing_service", "forecasting_service"],
            "explicabilite": [],
        }

    fournisseur, _cle_api, _modele = resolu

    if llm_client.supports_tool_calling(fournisseur):
        try:
            return _repondre_avec_outils(db, fournisseur, question)
        except llm_client.LLMError as exc:
            # Contexte construit seulement ici (repli) : en mode agent, les
            # outils fournissent eux-mêmes les données — inutile de recalculer
            # les deux prévisions ARIMA du contexte à chaque message.
            contexte = build_context_snapshot(db)
            return {
                "reponse": (
                    f"Le fournisseur LLM configuré ({fournisseur}) n'a pas répondu correctement "
                    f"({exc}) — voici en attendant la synthèse factuelle disponible pour répondre "
                    f"à : « {question} »\n\n{contexte}"
                ),
                "sources": ["analytics_service", "marketing_service"],
                "explicabilite": [],
            }

    # Fournisseur sans tool-calling dans cette intégration (cf.
    # llm_client._FOURNISSEURS_TOOL_CALLING) : chat simple avec contexte
    # injecté d'avance, comportement inchangé par rapport à avant la tâche #6.
    contexte = build_context_snapshot(db)
    prompt_utilisateur = f"Contexte (indicateurs réels de l'entreprise) :\n{contexte}\n\nQuestion : {question}"

    try:
        reponse_llm = llm_client.generate_completion(SYSTEM_PROMPT, prompt_utilisateur)
        return {
            "reponse": reponse_llm,
            "sources": ["analytics_service", "marketing_service", "forecasting_service", f"llm:{fournisseur}"],
            "explicabilite": [],
        }
    except llm_client.LLMError as exc:
        return {
            "reponse": (
                f"Le fournisseur LLM configuré ({fournisseur}) n'a pas répondu correctement "
                f"({exc}) — voici en attendant la synthèse factuelle disponible pour répondre "
                f"à : « {question} »\n\n{contexte}"
            ),
            "sources": ["analytics_service", "marketing_service", "forecasting_service"],
            "explicabilite": [],
        }

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

from sqlalchemy.orm import Session

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
    contexte = build_context_snapshot(db)
    resolu = llm_client.resolve_provider()

    if resolu is None:
        return {
            "reponse": (
                "Mode local (sans LLM configuré) — voici la synthèse des "
                f"indicateurs disponibles pour répondre à : « {question} »\n\n{contexte}"
            ),
            "sources": ["analytics_service", "marketing_service", "forecasting_service"],
            "explicabilite": [],
        }

    fournisseur, _cle_api, _modele = resolu
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

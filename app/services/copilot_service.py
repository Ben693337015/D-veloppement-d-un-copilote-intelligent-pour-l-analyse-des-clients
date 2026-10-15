"""Service métier — assistant conversationnel RAG mutualisé (Module D, Cadrage §CDCF 3.4).

Contexte injecté dans le prompt : synthèse texte des indicateurs réels
(KPIs, alertes stock/trésorerie, classement des clients par CA, segments RFM,
prévisions ventes/trésorerie,
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

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import HistoriqueSegmentation
from app.services import (
    analytics_service,
    comparaison_previsions,
    copilote_outils,
    forecasting_service,
    llm_client,
    marketing_service,
    sql_agent,
)

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
    "campagnes marketing grand public) et un segment \"Perdu\" (aucun achat "
    "depuis plus de la moitié de la fenêtre d'analyse sans être un client récurrent, "
    "ou aucun achat dans la fenêtre). Si une section « Données extraites de la base » "
    "est fournie, elle contient le résultat RÉEL d'une requête SQL exécutée pour cette "
    "question : appuie-toi d'abord dessus, cite les chiffres exacts et présente les listes "
    "sous forme de liste ou de tableau. Tes interlocuteurs sont des dirigeants, "
    "responsables et employés de PME, pas des data scientists : parle le langage du "
    "métier (ventes, marge, stock, trésorerie, clients) et traduis les notions techniques "
    "— dis « la prévision la plus fiable » plutôt que le nom d'un modèle (Prophet, ARIMA, "
    "XGBoost), « écart habituel d'environ X par jour » plutôt que RMSE ou MAE, « fourchette "
    "probable » plutôt qu'« intervalle de confiance ». Ne cite ces termes techniques que si "
    "l'utilisateur les emploie lui-même ou demande explicitement le détail de la méthode."
)

# Étape 1 de l'accès à la base : le LLM traduit la question en SQL.
def system_prompt_sql() -> str:
    """Prompt de l'étape SQL (fonction : le schéma montré dépend de la
    configuration, cf. COPILOTE_ACCES_EMAILS)."""
    return (
        "Tu traduis une question de gestion d'une PME en requêtes SQL PostgreSQL de "
        "LECTURE SEULE sur le schéma ci-dessous (aucune autre table ni colonne n'existe).\n"
        "Règles :\n"
        f"- de 1 à {sql_agent.REQUETES_MAX} requêtes, chacune dans son propre bloc ```sql``` ; "
        "plusieurs requêtes seulement si la question croise des sources différentes "
        "(ex. ventes ET stock d'un produit) ;\n"
        "- SELECT ou WITH uniquement ; jamais SELECT * (liste les colonnes utiles, alias en français) ;\n"
        "- chiffre d'affaires = SUM(quantite * prix_unitaire) avec WHERE NOT est_retour ;\n"
        "- pour les listes, trie de façon pertinente et ajoute LIMIT (50 au plus) ;\n"
        "- pour un classement de clients, regroupe par client_id (deux clients peuvent porter "
        "le même nom) et affiche le nom ;\n"
        "- les données peuvent être anciennes : pour « récent », « dernier mois », « cette année », "
        "raisonne à partir de MAX(date_transaction) (ou MAX(date_mouvement)) et non de CURRENT_DATE ;\n"
        "- arrondis les montants avec ROUND(x::numeric, 2) ;\n"
        "- les métriques des segmentations passées sont dans historique_segmentation "
        "(la plus récente : ORDER BY date_execution DESC LIMIT 1).\n"
        "Les prévisions, simulations et recommandations ne sont PAS en base : pour PRÉVOIR "
        "(ventes, trésorerie, sur un horizon ou un seuil donné), SIMULER (« et si les ventes "
        "baissent de 10 % ? ») ou RECOMMANDER (quoi commander, combien), utilise un OUTIL DE "
        "CALCUL, dans un bloc ```outil``` contenant un objet JSON, par exemple :\n"
        "```outil\n{\"nom\": \"prevision_ventes\", \"parametres\": {\"horizon_jours\": 60}}\n```\n"
        f"Au plus {copilote_outils.APPELS_MAX} outils. Outils disponibles :\n"
        + copilote_outils.catalogue_texte()
        + "\nTu peux combiner requêtes SQL (lire le passé) et outils (prévoir, simuler, "
        "recommander) dans la même réponse.\n"
        "Si la question ne nécessite ni la base ni un outil (salutation, explication d'une "
        "méthode), réponds exactement : AUCUNE.\n"
        "Sinon réponds UNIQUEMENT avec les blocs ```sql``` et/ou ```outil```.\n\n"
        "Schéma :\n" + sql_agent.schema_texte()
    )


_ESSAIS_SQL = 2  # 1 requête + 1 correction après refus ou erreur SQL

# Nombre de clients cités nommément par segment, pour illustrer une réponse
# concrète ("qui sont mes clients à risque ?") sans y dumper toute la table
# `clients` (qui peut compter des milliers de lignes sur une vraie PME).
_N_CLIENTS_EXEMPLE_PAR_SEGMENT = 5
_HORIZON_PREVISION_VENTES_CONTEXTE = 30  # même horizon que la page Ventes & Stocks (cache partagé)
_HORIZON_PREVISION_TRESORERIE_CONTEXTE = 30


_N_ALERTES_STOCK_CONTEXTE = 25


def _section_stock(db: Session) -> list[str]:
    """CORRIGÉ (test de bout en bout) : la liste COMPLÈTE des alertes était
    injectée dans le prompt (130 lignes, ~11 000 caractères sur 15 000 pour
    AdventureWorks ; sans limite pour un catalogue de milliers d'articles —
    coût, lenteur, risque de dépasser la fenêtre du modèle). On donne
    désormais le décompte par niveau, puis les alertes les plus urgentes
    (déjà triées par `get_stock_alertes`), bornées."""
    alertes = analytics_service.get_stock_alertes(db)
    n_critique = sum(a["niveau"] == "critique" for a in alertes)
    lignes = [
        f"Alertes stock actives: {len(alertes)} (critique: {n_critique}, "
        f"attention: {len(alertes) - n_critique})"
    ]
    for a in alertes[:_N_ALERTES_STOCK_CONTEXTE]:
        lignes.append(
            f"  - {a['nom_produit']} ({a['code_produit']}) : {a['quantite_disponible']} unité(s) "
            f"disponible(s), seuil critique {a['seuil_alerte']} — niveau {a['niveau']}"
        )
    if len(alertes) > _N_ALERTES_STOCK_CONTEXTE:
        lignes.append(
            f"  - ... et {len(alertes) - _N_ALERTES_STOCK_CONTEXTE} autre(s) alerte(s) moins urgente(s) "
            "non listée(s) (voir l'écran Ventes & Stocks)."
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
            f"{seg['montant_total_moyen']:.2f}, churn moyen "
            f"{'n/d' if seg['score_churn_moyen'] is None else format(seg['score_churn_moyen'], '.2f')}, "
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


_N_TOP_CLIENTS_CONTEXTE = 10


def _section_top_clients(db: Session) -> list[str]:
    """AJOUTÉ (octobre 2026) : sans cette section, une question comme « quels
    clients font mon chiffre d'affaires ? » ne trouvait dans le contexte que
    5 exemples par segment, choisis arbitrairement -> le LLM ne pouvait pas
    répondre sans inventer. On injecte le classement des plus gros clients
    et la concentration du CA (Pareto 80/20)."""
    top = analytics_service.get_top_clients_ca(db, limite=_N_TOP_CLIENTS_CONTEXTE)
    if not top["top"]:
        return ["Classement des clients par CA : aucun client rattaché à une vente."]
    lignes = [
        f"Concentration du CA : {top['nb_clients_80pct']} client(s) sur {top['nb_clients']} "
        f"({top['nb_clients_80pct'] / top['nb_clients']:.1%}) réalisent 80 % du CA rattaché à un client.",
        f"Top {len(top['top'])} clients par chiffre d'affaires (tout l'historique, retours exclus), "
        f"soit {sum(c['part_ca'] for c in top['top']):.2%} du CA à eux seuls :",
    ]
    for rang, c in enumerate(top["top"], 1):
        details = [f"{c['nb_factures']} facture(s)"]
        if c["segment_rfm"]:
            details.append(f"segment {c['segment_rfm']}")
        if c["recence_jours"] is not None:
            details.append(f"dernier achat il y a {c['recence_jours']}j")
        lignes.append(
            f"  {rang}. {c['client']} : CA {c['chiffre_affaires']:.2f} "
            f"({c['part_ca']:.2%} du CA), {', '.join(details)}"
        )
    return lignes


# Modèle de la prévision de TRÉSORERIE injectée dans le contexte : ARIMA,
# rapide (le chat doit rester réactif ; Prophet ajoute quelques secondes à
# chaque message) et validé de la même façon (RMSE/MAE). Les ventes, elles,
# utilisent la comparaison des 3 modèles, mise en cache (cf. ci-dessus).
_MODELE_PREVISION_CONTEXTE = "arima"


def _section_prevision_ventes(db: Session) -> list[str]:
    """MODIFIÉ (13/10/2026) : les 3 modèles (Prophet, ARIMA, XGBoost) et leur
    interprétation, exactement ce qu'affiche la page Ventes & Stocks (même
    calcul, même cache : instantané si la page a déjà été ouverte ou si un
    import vient d'être fait). Avant : ARIMA seul, 2 points."""
    try:
        res = comparaison_previsions.comparer_modeles(db, _HORIZON_PREVISION_VENTES_CONTEXTE)
    except Exception as exc:  # jamais casser le contexte pour une prévision en échec
        return [f"Prévision des ventes : indisponible ({exc})."]
    if not res["modeles"]:
        return ["Prévision des ventes : aucune vente importée."]
    lignes = [f"Prévision des ventes à {res['horizon_jours']} jours, 3 modèles comparés :"]
    lignes += [f"  - Synthèse : {t}" for t in res["synthese"]["texte"]]
    for m in res["modeles"]:
        if m["statut"] != "ok":
            lignes.append(f"  - {m['nom']} : calcul en échec ({m['erreur']}).")
            continue
        metriques = (
            f"RMSE {m['rmse_validation']:.2f}, MAE {m['mae_validation']:.2f}"
            if m["rmse_validation"] is not None else "non validé"
        )
        lignes.append(f"  - {m['nom']} ({m['modele_utilise']}, {metriques}) : " + " ".join(m["interpretation"]))
    meilleur = next((m for m in res["modeles"] if m["modele"] == res["synthese"]["meilleur_modele"]), None)
    if meilleur and meilleur["points"]:
        semaines = [meilleur["points"][i : i + 7] for i in range(0, len(meilleur["points"]), 7)]
        lignes.append(
            f"  - Détail par semaine ({meilleur['nom']}) : "
            + " ; ".join(
                f"du {s[0]['date_prevision']} au {s[-1]['date_prevision']} : "
                f"{sum(p['valeur_prevue'] for p in s):.0f}"
                for s in semaines
            )
        )
    return lignes


def _section_historique_segmentation(db: Session) -> list[str]:
    """AJOUT (13/10/2026) : métriques de la dernière segmentation (jusqu'ici
    perdues après affichage), pour expliquer le choix de k et la qualité."""
    derniere = (
        db.query(HistoriqueSegmentation).order_by(HistoriqueSegmentation.date_execution.desc()).first()
    )
    if derniere is None:
        return ["Historique de segmentation : aucune exécution enregistrée."]
    n_executions = db.query(HistoriqueSegmentation).count()
    qualite = ", ".join(
        f"{nom} {val:.3f}"
        for nom, val in (
            ("silhouette", derniere.silhouette),
            ("Davies-Bouldin", derniere.davies_bouldin),
            ("Calinski-Harabasz", derniere.calinski_harabasz),
        )
        if val is not None
    )
    parametre = f"k={derniere.k_choisi}" if derniere.k_choisi is not None else f"eps={derniere.eps_choisi}"
    lignes = [
        f"Dernière segmentation ({derniere.date_execution:%Y-%m-%d %H:%M}, {n_executions} exécution(s) "
        f"au total) : algorithme {derniere.algorithme}, {parametre}, {derniere.n_clients} clients actifs "
        f"+ {derniere.n_clients_hors_fenetre} hors fenêtre, fenêtre {derniere.fenetre_jours} j ; {qualite or 'métriques n/d'}."
    ]
    try:
        # Mode « auto » : synthèse de la comparaison des 3 algorithmes.
        for phrase in json.loads(derniere.synthese or "[]"):
            lignes.append(f"  - {phrase}")
    except (ValueError, TypeError):
        pass
    try:
        diagnostics = json.loads(derniere.diagnostics or "[]")
        if diagnostics:
            lignes.append(
                "  - Diagnostic par k testé (le k retenu maximise la silhouette) : "
                + " ; ".join(
                    f"k={d['k']} silhouette={d['silhouette']:.3f}" if d.get("silhouette") is not None
                    else f"k={d['k']} silhouette=n/d"
                    for d in diagnostics
                )
            )
    except (ValueError, KeyError, TypeError):
        pass
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
        *_section_top_clients(db),
        "",
        *_section_segments(db),
        *_section_historique_segmentation(db),
        "",
        *_section_prevision_ventes(db),
        "",
        *_section_tresorerie(db),
    ]
    return "\n".join(lignes)


def _executer(db: Session, sql: str) -> tuple[sql_agent.ResultatRequete | None, str | None]:
    """Valide puis exécute UNE requête ; renvoie (résultat, None) ou (None, motif)."""
    try:
        return sql_agent.executer_requete(db, sql_agent.valider_requete(sql)), None
    except sql_agent.RequeteRefusee as exc:
        return None, f"requête refusée : {exc}"
    except SQLAlchemyError as exc:
        return None, f"erreur SQL : {str(getattr(exc, 'orig', exc)).strip().splitlines()[0][:300]}"


def _executer_requetes(
    db: Session, question: str, prompt_sql: str, requetes: list[str]
) -> tuple[list[sql_agent.ResultatRequete], str | None]:
    """Exécute les requêtes SQL du plan ; celles refusées ou en erreur sont
    renvoyées au LLM avec l'erreur, qui les corrige UNE fois (les requêtes
    réussies sont conservées). Retourne (résultats, motif d'échec éventuel)."""
    resultats, echecs = [], []
    for sql in requetes:
        res, motif = _executer(db, sql)
        (resultats.append(res) if res else echecs.append((sql, motif)))
    if not echecs:
        return resultats, None

    # Correction : une seule tentative, pour les requêtes en échec uniquement.
    detail = "\n\n".join(f"```sql\n{sql}\n```\na échoué ({motif})" for sql, motif in echecs)
    message = (
        f"Question : {question}\n\nCes requêtes ont échoué :\n{detail}\n\n"
        "Corrige-les en respectant les règles (renvoie uniquement les requêtes corrigées)."
    )
    restant = sql_agent.REQUETES_MAX - len(resultats)
    motifs = []
    for sql in sql_agent.extraire_requetes(llm_client.generate_completion(prompt_sql, message))[:restant]:
        res, motif = _executer(db, sql)
        (resultats.append(res) if res else motifs.append(motif))
    if not motifs and len(resultats) > 0:
        return resultats, None
    return resultats, "; ".join(motifs or [m for _, m in echecs])


def _planifier(
    db: Session, question: str
) -> tuple[list[sql_agent.ResultatRequete], list[copilote_outils.AppelOutil], str | None]:
    """Orchestrateur du copilote (étape 1) : UN appel LLM de planification
    décide quoi faire pour cette question —
      - LIRE des données passées : requêtes SQL en lecture seule (sql_agent) ;
      - CALCULER : outils de prévision, simulation « et si », réapprovisionnement
        (copilote_outils), qui appellent les services métier ;
      - rien (salutation, explication) : AUCUNE.
    SQL et outils peuvent être combinés dans la même réponse. Aucun appel LLM
    de plus qu'avec le SQL seul (sauf la correction éventuelle d'une requête).

    Retourne (résultats SQL, appels d'outils exécutés, motif d'échec éventuel).
    `LLMError` n'est PAS interceptée ici : elle remonte à `answer_question`."""
    prompt_sql = system_prompt_sql()
    plan = llm_client.generate_completion(prompt_sql, f"Question : {question}")

    appels, erreurs_format = copilote_outils.extraire_appels(plan)
    appels = [copilote_outils.executer(db, appel) for appel in appels]

    requetes = sql_agent.extraire_requetes(plan)
    resultats, motif = _executer_requetes(db, question, prompt_sql, requetes) if requetes else ([], None)
    motifs = [m for m in (motif, *erreurs_format) if m]
    return resultats, appels, "; ".join(motifs) or None


def _interroger_base(db: Session, question: str) -> tuple[list[sql_agent.ResultatRequete], str | None]:
    """Compatibilité : partie SQL seule de `_planifier`."""
    resultats, _appels, motif = _planifier(db, question)
    return resultats, motif


def answer_question(db: Session, question: str) -> dict:
    """Point d'entrée de /api/v1/copilot/chat.

    Sans fournisseur LLM configuré (cf. `llm_client.resolve_provider`),
    retourne un résumé factuel basé sur le contexte plutôt qu'une
    génération libre, pour que l'API reste utilisable en local sans
    dépendance externe.

    Si un fournisseur EST configuré :
      1. (si COPILOTE_ACCES_BASE) le LLM peut interroger la base en lecture
         seule par une requête SQL validée (`_interroger_base`) ;
      2. il rédige la réponse avec la synthèse d'indicateurs ET les lignes
         réellement renvoyées par la base.
    Tout échec du fournisseur (réseau, authentification, quota, réponse
    inattendue — `llm_client.LLMError`) bascule sur la synthèse factuelle :
    JAMAIS de 500 brut. Un échec de la requête SQL, lui, n'empêche pas la
    réponse : le LLM répond avec la synthèse et le signale."""
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
            "mode": "local",
        }

    fournisseur, _cle_api, modele = resolu
    llm_client._local.modele = None  # modèle réellement utilisé (secours éventuel), cf. llm_client
    resultats, appels, motif_echec = [], [], None
    try:
        if settings.COPILOTE_ACCES_BASE:
            resultats, appels, motif_echec = _planifier(db, question)

        prompt_utilisateur = f"Contexte (indicateurs réels de l'entreprise) :\n{contexte}"
        for i, res in enumerate(resultats, 1):
            prompt_utilisateur += (
                f"\n\nDonnées extraites de la base pour cette question — requête {i} "
                f"(exécutée :\n{res.sql}) :\n{sql_agent.formater_pour_prompt(res)}"
            )
        for appel in appels:
            prompt_utilisateur += (
                "\n\nRésultat d'un calcul effectué pour cette question (chiffres exacts, à utiliser "
                "en priorité et à reformuler en langage métier) — "
                + copilote_outils.formater_pour_prompt(appel)
            )
        if motif_echec:
            prompt_utilisateur += (
                f"\n\nNote : une partie de l'interrogation de la base a échoué ({motif_echec}). "
                "Réponds avec les données disponibles et signale ce qui n'a pas pu être extrait."
            )
        prompt_utilisateur += f"\n\nQuestion : {question}"

        reponse_llm = llm_client.generate_completion(SYSTEM_PROMPT, prompt_utilisateur)
        modele = llm_client.dernier_modele_utilise() or modele
        sources = ["analytics_service", "marketing_service", "forecasting_service"]
        if resultats:
            sources.append("base:sql")
        sources += [f"outil:{a.nom}" for a in appels if a.resultat is not None]
        return {
            "reponse": reponse_llm,
            "sources": [*sources, f"llm:{fournisseur}"],
            "explicabilite": [],
            "mode": "llm",
            "fournisseur": fournisseur,
            "modele": modele,
            "requete_sql": resultats[0].sql if resultats else None,
            "requetes_sql": [r.sql for r in resultats],
            "lignes_base": sum(len(r.lignes) for r in resultats) if resultats else None,
            "outils_utilises": [
                {"nom": a.nom, "libelle": a.libelle or a.nom, "parametres": a.parametres,
                 "statut": "erreur" if a.erreur else "ok", "erreur": a.erreur}
                for a in appels
            ],
        }
    except llm_client.LLMError as exc:
        return {
            "reponse": (
                f"Le fournisseur LLM ({fournisseur}) est indisponible pour le moment : {exc}\n\n"
                f"En attendant, voici la synthèse factuelle disponible pour répondre à : "
                f"« {question} »\n\n{contexte}"
            ),
            "sources": ["analytics_service", "marketing_service", "forecasting_service"],
            "explicabilite": [],
            "mode": "secours",
            "fournisseur": fournisseur,
            "modele": modele,
        }

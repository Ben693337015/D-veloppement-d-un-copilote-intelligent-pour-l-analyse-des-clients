"""Évaluation automatique des 3 algorithmes de segmentation (mode « auto »).

Appelé par `rfm_clustering_service.run_full_pipeline(algorithme="auto")` :
K-Means, GMM et DBSCAN ont déjà été ajustés sur la MÊME matrice RFM
prétraitée (winsorisation, log1p, standardisation). Ce module :

1. Vérifie que chaque résultat est EXPLOITABLE pour du marketing
   (règles explicites, cf. `_eligibilite`) :
     - entre 2 et 8 groupes : au-delà, impossible de concevoir une action
       distincte par groupe (DBSCAN en produit souvent beaucoup) ;
     - au plus 10 % de clients non classés (bruit DBSCAN) ;
     - silhouette calculable.
2. Classe les algorithmes exploitables sur les 3 métriques internes :
   silhouette (↑), Davies-Bouldin (↓), Calinski-Harabasz (↑). Rang moyen,
   départage par la silhouette. Aucune métrique seule ne suffit : la
   silhouette favorise les groupes sphériques, Calinski-Harabasz les grands
   k, Davies-Bouldin les groupes compacts.
3. Produit une interprétation en français de chaque algorithme et une
   synthèse — DÉTERMINISTE (règles chiffrées, pas de LLM), donc
   reproductible et vérifiable en soutenance.

Échelle d'interprétation de la silhouette : Kaufman & Rousseeuw (1990),
« Finding Groups in Data » — > 0,70 structure forte ; 0,51–0,70 raisonnable ;
0,26–0,50 faible ; ≤ 0,25 pas de structure substantielle.
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

NOMS = {"kmeans": "K-Means", "gmm": "GMM", "dbscan": "DBSCAN"}
GROUPES_MIN, GROUPES_MAX = 2, 8
BRUIT_MAX = 0.10


def _f(x: float, d: int = 3) -> str:
    return f"{x:.{d}f}".replace(".", ",")


def _pct(x: float) -> str:
    if 0 < x < 0.001:
        return "moins de 0,1 %"
    if x >= 0.01 or x == 0:
        return f"{x * 100:.0f} %"
    return f"{x * 100:.1f} %".replace(".", ",")


def qualite_silhouette(s: float | None) -> str:
    if s is None:
        return "non mesurable"
    if s > 0.70:
        return "forte"
    if s > 0.50:
        return "raisonnable"
    if s > 0.25:
        return "faible"
    return "non substantielle"


def _eligibilite(n_groupes: int, part_bruit: float, silhouette: float | None) -> str | None:
    """Motif d'exclusion, ou None si le résultat est exploitable."""
    if silhouette is None:
        return "silhouette non calculable (un seul groupe exploitable)"
    if n_groupes < GROUPES_MIN:
        return f"{n_groupes} groupe : aucune segmentation"
    if n_groupes > GROUPES_MAX:
        return (
            f"{n_groupes} groupes : trop pour concevoir une action marketing par groupe "
            f"(maximum retenu : {GROUPES_MAX})"
        )
    if part_bruit > BRUIT_MAX:
        return f"{_pct(part_bruit)} de clients non classés (maximum retenu : {_pct(BRUIT_MAX)})"
    return None


def _interpreter(algo: str, res: dict, n_groupes: int, part_bruit: float, plus_petit: float,
                 repartition: dict[str, int]) -> list[str]:
    phrases = []
    diag = res["diagnostics"]
    sils = [(p.k, p.silhouette) for p in diag if p.silhouette is not None]
    if algo == "dbscan":
        phrases.append(
            f"Epsilon retenu : {_f(res['eps'])} (silhouette maximale parmi {len(diag)} valeurs testées, "
            "issues de la courbe k-distance) : "
            f"{n_groupes} groupe(s) dense(s) et {_pct(part_bruit)} de clients isolés (bruit)."
        )
    else:
        if sils:
            k_bas = min(sils, key=lambda t: t[1])
            phrases.append(
                f"k testé de {diag[0].k} à {diag[-1].k} : silhouette maximale à k={res['k']} "
                f"({_f(res['silhouette'])}), la plus basse à k={k_bas[0]} ({_f(k_bas[1])}). "
                f"k={res['k']} est donc retenu."
            )
        if algo == "gmm":
            k_bic = min(diag, key=lambda p: p.inertie).k
            phrases.append(
                f"Le BIC (critère probabiliste propre au GMM) est minimal à k={k_bic}"
                + (" : il confirme ce choix." if k_bic == res["k"] else
                   " ; la silhouette est préférée pour rester comparable aux autres algorithmes.")
            )
    if res["silhouette"] is not None:
        phrases.append(
            f"Silhouette {_f(res['silhouette'])} : structure {qualite_silhouette(res['silhouette'])} "
            "(échelle de Kaufman & Rousseeuw)"
            + (" — groupes réels mais aux frontières floues, courant sur des données RFM continues."
               if 0.25 < res["silhouette"] <= 0.50 else ".")
        )
    if res["davies_bouldin"] is not None:
        phrases.append(
            f"Davies-Bouldin {_f(res['davies_bouldin'], 2)} : "
            + ("groupes bien séparés (< 1)." if res["davies_bouldin"] < 1 else "groupes qui se chevauchent (≥ 1).")
        )
    if n_groupes >= 2:
        phrases.append(f"Le plus petit groupe réunit {_pct(plus_petit)} des clients segmentés.")
    total = sum(repartition.values()) or 1
    principaux = sorted(repartition.items(), key=lambda kv: -kv[1])[:3]
    autres = len(repartition) - len(principaux)
    phrases.append(
        "Segments obtenus : " + ", ".join(f"{seg} {_pct(n / total)}" for seg, n in principaux)
        + (f" et {autres} autre(s)." if autres > 0 else ".")
    )
    return phrases


def comparer(resultats: dict[str, dict], rfm_df: pd.DataFrame, fenetre_jours: int
             ) -> tuple[list[dict], str | None, list[str]]:
    """Retourne (comparaison par algorithme, algorithme recommandé, synthèse)."""
    from app.services.rfm_clustering_service import _assign_segments

    lignes, segments_par_algo = [], {}
    for algo, res in resultats.items():
        ligne = {
            "algorithme": algo, "nom": NOMS[algo], "statut": "ok" if res["ok"] else "erreur",
            "eligible": False, "motif_exclusion": None, "rang": None, "rang_moyen": None,
            "k_choisi": res["k"], "eps_choisi": res["eps"], "silhouette": res["silhouette"],
            "davies_bouldin": res["davies_bouldin"], "calinski_harabasz": res["calinski_harabasz"],
            "n_groupes": 0, "part_bruit": 0.0, "plus_petit_groupe": 0.0,
            "diagnostics": [asdict(p) for p in res["diagnostics"]],
            "repartition_segments": {}, "interpretation": [], "duree_s": round(res["duree_s"], 2),
        }
        if not res["ok"]:
            ligne["motif_exclusion"] = " ".join(res["avertissements"]) or "calcul impossible"
            lignes.append(ligne)
            continue
        labels = np.asarray(res["labels"])
        non_bruit = labels[labels != -1]
        tailles = pd.Series(non_bruit).value_counts()
        n_groupes = int(tailles.size)
        part_bruit = float((labels == -1).mean())
        plus_petit = float(tailles.min() / len(labels)) if n_groupes else 0.0
        copie = rfm_df.copy()
        copie["cluster_id"] = labels
        segments = _assign_segments(copie, fenetre_jours)
        segments_par_algo[algo] = segments
        repartition = {k: int(v) for k, v in segments.value_counts().items()}
        motif = _eligibilite(n_groupes, part_bruit, res["silhouette"])
        ligne.update(
            n_groupes=n_groupes, part_bruit=part_bruit, plus_petit_groupe=plus_petit,
            repartition_segments=repartition, eligible=motif is None, motif_exclusion=motif,
            interpretation=_interpreter(algo, res, n_groupes, part_bruit, plus_petit, repartition),
        )
        lignes.append(ligne)

    eligibles = [ligne for ligne in lignes if ligne["eligible"]]
    if not eligibles:
        return lignes, None, [
            "Aucun algorithme n'a produit une segmentation exploitable sur ces données "
            "(trop peu de clients ou profils trop homogènes) : segments non modifiés."
        ]

    # Classement : rang moyen sur les 3 métriques (1 = meilleur), départage par la silhouette.
    df = pd.DataFrame(eligibles).set_index("algorithme")
    rangs = pd.concat(
        [
            df["silhouette"].rank(ascending=False, method="min"),
            df["davies_bouldin"].rank(ascending=True, method="min"),
            df["calinski_harabasz"].rank(ascending=False, method="min"),
        ],
        axis=1,
    ).mean(axis=1)
    ordre = sorted(eligibles, key=lambda e: (rangs[e["algorithme"]], -(e["silhouette"] or 0)))
    for position, ligne in enumerate(ordre, 1):
        ligne["rang"] = position
        ligne["rang_moyen"] = float(rangs[ligne["algorithme"]])
    meilleur = ordre[0]

    parametre = f"k={meilleur['k_choisi']}" if meilleur["k_choisi"] is not None else f"eps={_f(meilleur['eps_choisi'])}"
    synthese = [
        f"Algorithme recommandé : {meilleur['nom']} ({parametre}) — "
        + ("seul algorithme exploitable." if len(ordre) == 1 else
           "meilleur classement moyen sur les 3 métriques (silhouette, Davies-Bouldin, "
           "Calinski-Harabasz) parmi les algorithmes exploitables.")
    ]
    if len(ordre) > 1:
        synthese.append("Classement : " + " · ".join(
            f"{e['rang']}. {e['nom']} (silhouette {_f(e['silhouette'])}, DB {_f(e['davies_bouldin'], 2)})"
            for e in ordre))
    for ligne in lignes:
        if not ligne["eligible"]:
            synthese.append(f"Écarté : {ligne['nom']} — {ligne['motif_exclusion']}.")
    synthese.append(
        f"Qualité de la structure retenue : {qualite_silhouette(meilleur['silhouette'])} "
        f"(silhouette {_f(meilleur['silhouette'])})."
    )
    if len(segments_par_algo) > 1:
        accord = float((pd.concat(segments_par_algo.values(), axis=1).nunique(axis=1) == 1).mean())
        synthese.append(
            f"{_pct(accord)} des clients reçoivent la même étiquette quel que soit l'algorithme : "
            "les règles RFM individuelles (récence, fréquence) pèsent plus que le choix de "
            "l'algorithme, qui départage surtout les clients Fidèles et Occasionnels."
        )
    return lignes, meilleur["algorithme"], synthese

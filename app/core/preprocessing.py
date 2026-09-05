"""Utilitaires de prétraitement statistique partagés entre les deux
sous-projets (Maslaw : séries de ventes ; Abdoulmadjid : RFM/clustering).

Volontairement placé dans `app/core/` plutôt que dans l'un des deux
`services/` métier : les deux sous-projets doivent pouvoir faire évoluer
leur logique indépendamment (cf. README §3 "Cela permet à Maslaw et
Abdoulmadjid de faire évoluer leurs modules sans se marcher dessus"), donc
aucun des deux services ne doit dépendre du module de l'autre — seulement
d'un utilitaire neutre et sans état.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def winsoriser(serie: pd.Series, percentile: float = 99.0) -> tuple[pd.Series, int]:
    """PRÉTRAITEMENT — plafonne (winsorise) une série pour limiter l'impact
    des valeurs extrêmes (erreurs de saisie, client/jour hors norme) sur un
    modèle sensible à l'échelle (distance euclidienne du K-Means, ajustement
    Prophet/ARIMA), SANS supprimer aucune ligne.

    BUG CONCRET ÉVITÉ (vérifié par test, module RFM) : un SEUL client avec un
    montant aberrant (faute de frappe sur un prix unitaire) forçait K-Means à
    isoler cet unique client dans son propre cluster et à fusionner TOUS les
    autres clients — pourtant très divers — en un unique cluster
    indifférencié. Un SEUL jour de vente aberrant a le même effet destructeur
    sur un modèle de prévision (RMSE multiplié par 100+, intervalles de
    confiance délirants — vérifié par test, module prévision).

    BUG ÉVITÉ (2e itération) : un simple plafond au Pe percentile
    (`np.percentile`) est quasi inopérant sur un PETIT jeu de données — avec
    peu d'observations, le 99e percentile s'interpole quasiment jusqu'à la
    valeur maximale elle-même. Beaucoup de PME ont justement PEU de clients
    ou peu d'historique — le cas visé en premier lieu par ce projet. On
    combine donc le percentile avec la clôture de Tukey (Q3 + 1,5×IQR),
    robuste au nombre d'observations car basée sur l'écart interquartile de
    la masse "normale" des données, et on retient le plafond le PLUS BAS des
    deux (le plus protecteur). Un garde-fou empêche de plafonner sous la
    médiane (évite un cap absurde sur une distribution très concentrée où
    IQR ≈ 0).

    Retourne (série plafonnée, nombre de valeurs effectivement plafonnées).
    """
    if serie.empty or serie.nunique() < 2:
        return serie, 0

    plafond_percentile = float(np.percentile(serie, percentile))
    q1, q3 = np.percentile(serie, [25, 75])
    iqr = q3 - q1
    plafond = min(plafond_percentile, float(q3 + 1.5 * iqr)) if iqr > 0 else plafond_percentile
    plafond = max(plafond, float(serie.median()))  # garde-fou

    n_plafonnes = int((serie > plafond).sum())
    if n_plafonnes == 0:
        return serie, 0
    return serie.clip(upper=plafond), n_plafonnes

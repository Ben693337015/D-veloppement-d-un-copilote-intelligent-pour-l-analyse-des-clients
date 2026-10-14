"""Analyse complémentaire du rapport (tableau 9 et figure 10) : erreurs des
trois modèles de prévision comparées à deux références naïves, avec le MASE,
sur la série complète puis sur la série arrêtée à une date donnée.

Utilise les fonctions du prototype (même série journalière, même découpage
80/20, mêmes modèles) sur les ventes déjà importées dans la base.

    python -m scripts.analyse_robustesse_previsions                 # date de coupure par défaut
    python -m scripts.analyse_robustesse_previsions --coupure 2013-12-28

Références naïves :
  - « naïf J-7 (1 pas) » : la valeur réelle de J-7, comparable à XGBoost dont
    les variables retardées sont les vraies valeurs pendant la validation ;
  - « naïf dernière semaine » : la dernière semaine connue répétée sur toute
    la période de test, comparable à ARIMA et Prophet.
MASE = MAE / MAE de la prévision naïve saisonnière (J-7) sur l'entraînement
(Hyndman et Koehler, 2006) ; en dessous de 1, le modèle bat la référence.
"""

from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd

from app.core.database import SessionLocal
from app.services import forecasting_service as fs


def _erreurs(y_vrai: np.ndarray, y_pred: np.ndarray) -> tuple[float, float]:
    return float(np.sqrt(np.mean((y_vrai - y_pred) ** 2))), float(np.mean(np.abs(y_vrai - y_pred)))


def evaluer(serie: pd.DataFrame) -> dict:
    train, test = fs._decoupe_train_test(serie)
    y, y_test = serie["y"].to_numpy(), test["y"].to_numpy()
    n, n_test = len(serie), len(test)
    y_train = train["y"].to_numpy()
    denom_mase = float(np.mean(np.abs(y_train[7:] - y_train[:-7])))

    lignes = {}
    for nom, fonction in (("Prophet", fs._previsions_prophet), ("ARIMA(1,1,1)", fs._previsions_arima),
                          ("XGBoost (1 pas)", fs._previsions_xgboost)):
        _, rmse, mae = fonction(serie, 30)
        lignes[nom] = (rmse, mae)
    lignes["Naïf J-7 (1 pas)"] = _erreurs(y_test, y[n - n_test - 7 : n - 7])
    lignes["Naïf dernière semaine"] = _erreurs(y_test, np.resize(y_train[-7:], n_test))
    return {
        "periode": f"{serie['ds'].min().date()} -> {serie['ds'].max().date()}",
        "test": f"{test['ds'].min().date()} -> {test['ds'].max().date()} ({n_test} j)",
        "ca_journalier_moyen_test": round(float(y_test.mean())),
        "modeles": {k: {"rmse": round(r), "mae": round(a), "mase": round(a / denom_mase, 2)} for k, (r, a) in lignes.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--coupure", default="2013-12-28", help="dernier jour gardé pour la série tronquée")
    args = parser.parse_args()
    logging.disable(logging.WARNING)

    with SessionLocal() as db:
        serie, _ = fs._serie_ca_journalier(db)
    if serie.empty:
        raise SystemExit("Aucune vente en base : importer d'abord ventes.csv.")

    resultats = {
        "serie_complete": evaluer(serie),
        "serie_tronquee": evaluer(serie[serie["ds"] <= args.coupure].reset_index(drop=True)),
    }
    for cle, res in resultats.items():
        print(f"\n== {cle} : {res['periode']} ; test {res['test']} ; CA moyen du test {res['ca_journalier_moyen_test']}")
        print(f"{'Modèle':<24}{'RMSE':>10}{'MAE':>10}{'MASE':>8}")
        for nom, m in sorted(res["modeles"].items(), key=lambda kv: kv[1]["rmse"]):
            print(f"{nom:<24}{m['rmse']:>10}{m['mae']:>10}{m['mase']:>8}")
    print("\n" + json.dumps(resultats, ensure_ascii=False))


if __name__ == "__main__":
    main()

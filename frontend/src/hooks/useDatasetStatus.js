import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { getKpis } from "../api/client";

/**
 * Sait si un jeu de données a déjà été importé (nb_clients > 0). Sert à
 * activer/désactiver dans la barre latérale les pages qui ont besoin de
 * données (Segmentation, Ventes & Stocks, Rapport) — principe de
 * "progressive disclosure" repéré sur l'app de référence RFM Analytics
 * Pro (sections grisées tant qu'aucun fichier n'est chargé).
 *
 * Re-vérifié à chaque changement de page : le parcours normal est
 * "importer sur /donnees puis revenir à l'accueil" — sans ce re-check,
 * les liens resteraient grisés après un import réussi tant que la page
 * n'est pas rechargée manuellement.
 *
 * En cas d'erreur réseau, on NE désactive PAS les liens (statut "unknown")
 * pour ne pas bloquer l'utilisateur à cause d'un problème transitoire —
 * useApiHealth() donne déjà un signal clair si l'API est injoignable.
 */
export function useDatasetStatus() {
  const [state, setState] = useState({ status: "loading", kpis: null });
  const { pathname } = useLocation();

  useEffect(() => {
    let annule = false;
    getKpis()
      .then((k) => {
        if (annule) return;
        setState({ status: k.nb_clients > 0 ? "loaded" : "empty", kpis: k });
      })
      .catch(() => !annule && setState({ status: "unknown", kpis: null }));
    return () => {
      annule = true;
    };
  }, [pathname]);

  return state;
}

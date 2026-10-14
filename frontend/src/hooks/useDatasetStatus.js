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
/** Événement émis après un import réussi (cf. pages/Donnees.jsx) : la barre
 * latérale se met à jour sans attendre un changement de page. CORRIGÉ le
 * 13/10/2026 — avant, les liens restaient verrouillés après l'import tant
 * que l'utilisateur ne repassait pas par l'Accueil (et un lien verrouillé
 * ne permet justement pas de changer de page). */
export const EVENEMENT_DONNEES_MODIFIEES = "copilote:donnees-modifiees";

export function signalerDonneesModifiees() {
  window.dispatchEvent(new Event(EVENEMENT_DONNEES_MODIFIEES));
}

export function useDatasetStatus() {
  const [state, setState] = useState({ status: "loading", kpis: null });
  const { pathname } = useLocation();
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const relire = () => setVersion((v) => v + 1);
    window.addEventListener(EVENEMENT_DONNEES_MODIFIEES, relire);
    return () => window.removeEventListener(EVENEMENT_DONNEES_MODIFIEES, relire);
  }, []);

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
  }, [pathname, version]);

  return state;
}

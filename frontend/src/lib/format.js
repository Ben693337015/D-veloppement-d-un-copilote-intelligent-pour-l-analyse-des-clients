/**
 * Formatage des nombres pour les cartes KPI.
 *
 * Problème constaté : un grand nombre entier (ex. chiffre d'affaires
 * "26 253 352") affiché tel quel en police tabulaire 26px déborde ou se
 * retrouve collé au bord de la carte sur les grilles à 3-5 colonnes,
 * surtout sur petits écrans — voir captures du 2026-08-23.
 *
 * Principe retenu : au-delà d'un seuil de lisibilité (100 000), on bascule
 * en notation compacte française ("26,3 M") qui tient toujours sur une
 * ligne, quelle que soit la largeur de la carte. La valeur exacte n'est
 * jamais perdue : elle reste disponible en `full` pour être affichée en
 * sous-texte (`hint` de KpiCard) et/ou en info-bulle au survol — utile
 * dans un contexte académique où le chiffre précis doit rester consultable.
 */

const FORMATEUR_EXACT = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const FORMATEUR_COMPACT = new Intl.NumberFormat("fr-FR", {
  notation: "compact",
  maximumFractionDigits: 1,
});

const SEUIL_COMPACT = 100_000;

/** Formate un entier avec séparateurs de milliers fr-FR ("26 253 352"). */
export function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return FORMATEUR_EXACT.format(value);
}

/**
 * Formate une valeur de KPI de façon "élégante" : compacte si le nombre
 * est grand, exacte sinon. Retourne toujours les deux représentations.
 *
 * @returns {{ display: string, full: string|null }}
 *   `display` : ce qu'on affiche dans la carte (jamais de débordement).
 *   `full` : la valeur exacte, à afficher en `hint`/`title` — `null` si
 *   `display` est déjà la valeur exacte (pas la peine de la répéter).
 */
export function formatKpiValue(value) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return { display: "—", full: null };
  }
  const exact = FORMATEUR_EXACT.format(value);
  if (Math.abs(value) < SEUIL_COMPACT) {
    return { display: exact, full: null };
  }
  return { display: FORMATEUR_COMPACT.format(value), full: exact };
}

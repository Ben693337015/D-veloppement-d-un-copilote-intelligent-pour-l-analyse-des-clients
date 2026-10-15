import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { PrevisionsVentes } from "../components/PrevisionsVentes";

const { getComparaisonPrevisions } = vi.hoisted(() => ({ getComparaisonPrevisions: vi.fn() }));
vi.mock("../api/client", () => ({ getComparaisonPrevisions }));

const point = (j, v, bornes) => ({
  date_prevision: `2024-03-0${j}`,
  valeur_prevue: v,
  borne_basse: bornes ? v - 10 : null,
  borne_haute: bornes ? v + 10 : null,
});
const modele = (modele, nom, rmse, phrase, extra = {}) => ({
  modele, nom, statut: "ok", erreur: null, modele_utilise: nom, horizon_jours: 2,
  points: [point(1, 100, modele !== "xgboost"), point(2, 110, modele !== "xgboost")],
  rmse_validation: rmse, mae_validation: rmse / 2, avertissements: [], interpretation: [phrase],
  indicateurs: { total_prevu: 210, fiabilite: "bonne", sens: "hausse" }, ...extra,
});

const REPONSE = {
  horizon_jours: 30,
  historique: [{ date: "2024-02-28", valeur: 95 }, { date: "2024-02-29", valeur: 98 }],
  modeles: [
    modele("prophet", "Prophet", 30, "Interprétation Prophet."),
    modele("arima", "ARIMA", 20, "Interprétation ARIMA."),
    modele("xgboost", "XGBoost", 10, "Interprétation XGBoost."),
  ],
  synthese: { meilleur_modele: "xgboost", texte: ["Modèle le plus fiable sur vos données : XGBoost."] },
  calcule_le: "2026-10-13T08:00:00",
  depuis_cache: true,
};

test("lance les 3 modèles sans action de l'utilisateur et affiche graphiques + interprétations", async () => {
  getComparaisonPrevisions.mockResolvedValueOnce(REPONSE);
  render(<PrevisionsVentes />);
  expect(screen.getByRole("status")).toHaveTextContent(/calcul de la prévision à 30 jours en cours/i);
  expect(getComparaisonPrevisions).toHaveBeenCalledWith(30);

  expect(await screen.findByTestId("synthese-previsions")).toHaveTextContent("Modèle le plus fiable sur vos données : XGBoost.");
  for (const [id, texte] of [["prophet", "Interprétation Prophet."], ["arima", "Interprétation ARIMA."], ["xgboost", "Interprétation XGBoost."]]) {
    expect(screen.getByTestId(`prevision-${id}`)).toHaveTextContent(texte);
  }
  expect(within(screen.getByTestId("prevision-xgboost")).getByText(/le plus fiable/i)).toBeInTheDocument();
  expect(within(screen.getByTestId("prevision-prophet")).queryByText(/le plus fiable/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /générer la prévision/i })).not.toBeInTheDocument();
});

test("changer l'horizon relance le calcul", async () => {
  getComparaisonPrevisions.mockResolvedValue(REPONSE);
  render(<PrevisionsVentes />);
  await screen.findByTestId("synthese-previsions");
  await userEvent.setup().click(screen.getByRole("button", { name: "90 j" }));
  expect(getComparaisonPrevisions).toHaveBeenLastCalledWith(90);
});

test("un modèle en échec est signalé sans masquer les autres", async () => {
  const r = structuredClone(REPONSE);
  r.modeles[2] = { ...r.modeles[2], statut: "erreur", erreur: "panne simulée", points: [], interpretation: [] };
  getComparaisonPrevisions.mockResolvedValueOnce(r);
  render(<PrevisionsVentes />);
  expect(await screen.findByTestId("prevision-xgboost")).toHaveTextContent(/n'a pas pu être calculé : panne simulée/);
  expect(screen.getByTestId("prevision-arima")).toHaveTextContent("Interprétation ARIMA.");
});


// ── Vue dirigeant (par défaut) et mode expert ───────────────────────────────

const LECTURE = {
  disponible: true,
  titre: "Ventes attendues sur les 30 prochains jours",
  resume: "Environ 210 de ventes attendues sur 30 jours, +6 % par rapport à votre rythme des 30 derniers jours.",
  total_prevu: 210, moyenne_jour: 105, evolution: 0.06, sens_evolution: "hausse",
  evolution_texte: "+6 % par rapport à votre rythme des 30 derniers jours",
  fiabilite: "bonne", fiabilite_texte: "Sur les semaines passées, la prévision d'une journée s'est écartée en moyenne de ±5 des ventes réelles.",
  ecart_jour: 5, concordance: "solide", scenario_bas: 190, scenario_haut: 230,
  conseils: ["Ventes en hausse attendue : vérifiez que vos stocks suivront (alertes de stock ci-dessus)."],
  modele_retenu: "xgboost",
};
const AVEC_LECTURE = { ...REPONSE, lecture_dirigeant: LECTURE };

beforeEach(() => window.localStorage.clear());

test("par défaut : vue dirigeant en langage métier, sans nom de modèle ni métrique", async () => {
  getComparaisonPrevisions.mockResolvedValueOnce(AVEC_LECTURE);
  const { container } = render(<PrevisionsVentes />);
  const vue = await screen.findByTestId("vue-dirigeant");
  expect(vue).toHaveTextContent(LECTURE.resume);
  expect(vue).toHaveTextContent("En hausse");
  expect(vue).toHaveTextContent("Bonne");
  expect(vue).toHaveTextContent(/fourchette prudente/i);
  expect(screen.getByTestId("conseils-previsions")).toHaveTextContent("vérifiez que vos stocks suivront");
  for (const jargon of [/RMSE/, /MAE/, /Prophet/, /ARIMA/, /XGBoost/]) {
    expect(container).not.toHaveTextContent(jargon);
  }
  expect(screen.queryByTestId("details-techniques")).not.toBeInTheDocument();
});

test("le bouton « détails techniques » affiche les 3 méthodes et leurs mesures, et s'en souvient", async () => {
  getComparaisonPrevisions.mockResolvedValue(AVEC_LECTURE);
  const { unmount } = render(<PrevisionsVentes />);
  await screen.findByTestId("vue-dirigeant");
  await userEvent.setup().click(screen.getByRole("button", { name: /afficher les détails techniques/i }));
  const details = screen.getByTestId("details-techniques");
  expect(details).toHaveTextContent("Modèle le plus fiable sur vos données : XGBoost.");
  expect(within(screen.getByTestId("prevision-arima")).getByText("RMSE")).toBeInTheDocument();
  unmount();

  render(<PrevisionsVentes />);
  expect(await screen.findByTestId("details-techniques")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /masquer les détails techniques/i })).toBeInTheDocument();
});

test("prévision indisponible : message et conseil en langage simple", async () => {
  getComparaisonPrevisions.mockResolvedValueOnce({
    ...REPONSE,
    lecture_dirigeant: { disponible: false, titre: "Ventes attendues", resume: "Pas encore assez d'historique de ventes pour une prévision fiable.", conseils: ["Importez au moins quelques semaines de ventes pour obtenir une prévision."] },
  });
  render(<PrevisionsVentes />);
  expect(await screen.findByTestId("vue-dirigeant")).toHaveTextContent("Pas encore assez d'historique");
});

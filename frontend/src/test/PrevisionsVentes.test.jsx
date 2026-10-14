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
  expect(screen.getByRole("status")).toHaveTextContent(/calcul des 3 modèles en arrière-plan/i);
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

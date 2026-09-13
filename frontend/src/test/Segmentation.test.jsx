import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { Segmentation } from "../pages/Segmentation";

const { runRfmPipeline } = vi.hoisted(() => ({ runRfmPipeline: vi.fn() }));

vi.mock("../api/client", () => ({
  runRfmPipeline,
  getSegmentsSummary: vi.fn().mockResolvedValue([
    {
      segment_rfm: "Fidèle",
      nb_clients: 20,
      montant_total_moyen: 1500,
      score_churn_moyen: 0.15,
      action_marketing_recommandee: "Programme de fidélité, offres premium",
    },
    {
      segment_rfm: "À risque",
      nb_clients: 20,
      montant_total_moyen: 400,
      score_churn_moyen: 0.9,
      action_marketing_recommandee: "Campagne de relance ciblée",
    },
  ]),
  getClients: vi.fn().mockResolvedValue([
    { client_id: "1", code_client_externe: "C1", segment_rfm: "Fidèle", recence_jours: 2, frequence_achats: 10, montant_total: 1800, score_churn: 0.1 },
  ]),
}));

test("lance le pipeline RFM (K-Means) et affiche les résultats", async () => {
  runRfmPipeline.mockResolvedValue({
    algorithme_utilise: "kmeans",
    n_clients: 80,
    fenetre_jours: 365,
    date_reference: "2024-06-01",
    k_choisi: 3,
    eps_choisi: null,
    silhouette_choisi: 0.745,
    ecrit_en_base: true,
    avertissements: [],
    diagnostics: [
      { k: 2, inertie: 110, silhouette: 0.56 },
      { k: 3, inertie: 27, silhouette: 0.745 },
      { k: 4, inertie: 19, silhouette: 0.69 },
    ],
  });

  const user = userEvent.setup();
  render(<Segmentation />);

  await user.click(screen.getByRole("button", { name: /lancer le pipeline rfm/i }));

  await waitFor(() =>
    expect(screen.getByText(/pipeline rfm \(kmeans\) · 80 clients · k=3 · sil\.=0\.745/i)).toBeInTheDocument(),
  );

  expect((await screen.findAllByText("Fidèle")).length).toBeGreaterThan(0);
  expect(screen.getAllByText("À risque").length).toBeGreaterThan(0);
  expect(screen.getByText("C1")).toBeInTheDocument();
});

test("bascule sur DBSCAN et affiche epsilon plutôt que k", async () => {
  runRfmPipeline.mockResolvedValue({
    algorithme_utilise: "dbscan",
    n_clients: 83,
    fenetre_jours: 365,
    date_reference: "2024-06-01",
    k_choisi: null,
    eps_choisi: 0.541,
    silhouette_choisi: 0.487,
    ecrit_en_base: true,
    avertissements: [
      "3 client(s) isolé(s) comme atypique(s) par DBSCAN (segment 'Atypique (B2B/grossiste)').",
    ],
    diagnostics: [
      { k: 1, inertie: 0.47, silhouette: 0.2, n_clusters_trouves: 4, n_bruit: 18 },
      { k: 4, inertie: 0.541, silhouette: 0.487, n_clusters_trouves: 2, n_bruit: 11 },
    ],
  });

  const user = userEvent.setup();
  render(<Segmentation />);

  // Ouvrir le panneau Paramètres pour changer d'algorithme.
  await user.click(screen.getByRole("button", { name: /paramètres/i }));
  await user.selectOptions(screen.getByLabelText(/algorithme/i), "dbscan");
  await user.click(screen.getByRole("button", { name: /lancer le pipeline rfm/i }));

  await waitFor(() =>
    expect(screen.getByText(/pipeline rfm \(dbscan\) · 83 clients · ε=0\.541 · sil\.=0\.487/i)).toBeInTheDocument(),
  );
  expect(runRfmPipeline).toHaveBeenCalledWith(expect.objectContaining({ algorithme: "dbscan" }));
  expect(screen.getByText(/isolé\(s\) comme atypique/i)).toBeInTheDocument();
});

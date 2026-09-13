import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Accueil } from "../pages/Accueil";

vi.mock("../api/client", () => ({
  getKpis: vi.fn().mockResolvedValue({
    nb_clients: 42,
    nb_transactions: 310,
    chiffre_affaires_total: 15230.5,
    stock_total_unites: 980,
    solde_tresorerie_estime: 4200,
  }),
  getStockAlertes: vi.fn().mockResolvedValue([{ code_produit: "P1" }, { code_produit: "P2" }]),
}));

test("affiche les KPIs réels une fois chargés", async () => {
  render(
    <MemoryRouter>
      <Accueil />
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText("42")).toBeInTheDocument());
  expect(screen.getByText("310")).toBeInTheDocument();
  expect(screen.getByText("2")).toBeInTheDocument(); // nb alertes
});

test("affiche une invitation à importer si aucune donnée", async () => {
  const { getKpis, getStockAlertes } = await import("../api/client");
  getKpis.mockResolvedValueOnce({
    nb_clients: 0,
    nb_transactions: 0,
    chiffre_affaires_total: 0,
    stock_total_unites: 0,
    solde_tresorerie_estime: 0,
  });
  getStockAlertes.mockResolvedValueOnce([]);

  render(
    <MemoryRouter>
      <Accueil />
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText(/aucune donnée importée/i)).toBeInTheDocument());
});

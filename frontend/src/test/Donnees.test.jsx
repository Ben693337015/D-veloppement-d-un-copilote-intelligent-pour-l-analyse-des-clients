import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Donnees } from "../pages/Donnees";

vi.mock("../api/client", () => ({
  previewDataset: vi.fn().mockResolvedValue({
    nom_fichier_colonnes: ["Date Vente", "Client Ref", "Montant Ligne"],
    n_lignes: 129,
    n_colonnes: 3,
    colonnes: [],
    apercu_lignes: [{ "Date Vente": "2024-01-05", "Client Ref": "CL-001", "Montant Ligne": "150.0" }],
    mapping_suggere: {
      transactions: { date_col: "Date Vente", client_col: "Client Ref", montant_col: "Montant Ligne" },
      stocks: {},
      tresorerie: {},
    },
  }),
  importDataset: vi.fn().mockResolvedValue({
    cible: "transactions",
    n_lignes_lues: 129,
    n_lignes_importees: 129,
    n_lignes_rejetees: 0,
    n_doublons_supprimes: 0,
    n_enregistrements_crees: 30,
    n_enregistrements_maj: 0,
    avertissements: [],
  }),
}));

test("upload d'un fichier -> mapping auto-détecté -> import réussi", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <Donnees />
    </MemoryRouter>,
  );

  const fichier = new File(["a,b,c\n1,2,3"], "ventes.csv", { type: "text/csv" });
  const input = document.querySelector('input[type="file"]');
  await user.upload(input, fichier);

  // Le mapping suggéré doit être pré-rempli
  await waitFor(() => expect(screen.getByText("129")).toBeInTheDocument());
  expect(screen.getByDisplayValue("Date Vente")).toBeInTheDocument();
  expect(screen.getByDisplayValue("Client Ref")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /lancer l'import/i }));

  await waitFor(() => expect(screen.getByText(/import terminé/i)).toBeInTheDocument());
  expect(screen.getByText(/enregistrements créés\s*:\s*30/i)).toBeInTheDocument();
});

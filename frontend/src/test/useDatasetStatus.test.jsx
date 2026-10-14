import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { signalerDonneesModifiees, useDatasetStatus } from "../hooks/useDatasetStatus";

const { getKpis } = vi.hoisted(() => ({ getKpis: vi.fn() }));
vi.mock("../api/client", () => ({ getKpis }));

function Statut() {
  const { status } = useDatasetStatus();
  return <p data-testid="statut">{status}</p>;
}

test("la barre latérale se déverrouille dès la fin de l'import, sans changer de page", async () => {
  getKpis.mockResolvedValueOnce({ nb_clients: 0 }).mockResolvedValueOnce({ nb_clients: 30 });
  render(
    <MemoryRouter>
      <Statut />
    </MemoryRouter>,
  );
  expect(await screen.findByText("empty")).toBeInTheDocument();
  await act(async () => signalerDonneesModifiees());
  expect(await screen.findByText("loaded")).toBeInTheDocument();
  expect(getKpis).toHaveBeenCalledTimes(2);
});

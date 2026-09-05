import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { Copilote } from "../pages/Copilote";

vi.mock("../api/client", () => ({
  poserQuestion: vi.fn().mockResolvedValue({
    reponse: "CA total: 13350.71\nClients: 30",
    sources: ["analytics_service"],
    explicabilite: [],
  }),
}));

test("envoie une question et affiche la réponse du copilote", async () => {
  const user = userEvent.setup();
  render(<Copilote />);

  const champ = screen.getByPlaceholderText(/posez votre question/i);
  await user.type(champ, "Quel est mon chiffre d'affaires ?");
  await user.click(screen.getByRole("button", { name: /envoyer/i }));

  expect(await screen.findByText(/quel est mon chiffre d'affaires/i)).toBeInTheDocument();
  expect(await screen.findByText(/ca total: 13350\.71/i)).toBeInTheDocument();
});

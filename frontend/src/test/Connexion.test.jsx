import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { Connexion } from "../pages/Connexion";

const { login, register } = vi.hoisted(() => ({ login: vi.fn(), register: vi.fn() }));

vi.mock("../api/client", () => ({ login, register }));

function renderConnexion(onConnecte = vi.fn()) {
  return render(
    <MemoryRouter>
      <Connexion onConnecte={onConnecte} />
    </MemoryRouter>,
  );
}

test("connexion réussie appelle login() et onConnecte()", async () => {
  login.mockResolvedValue({ access_token: "jeton", token_type: "bearer" });
  const onConnecte = vi.fn();
  const user = userEvent.setup();
  renderConnexion(onConnecte);

  await user.type(screen.getByLabelText(/email/i), "paul@pme-exemple.cm");
  await user.type(screen.getByLabelText(/mot de passe/i), "motdepasse123");
  await user.click(screen.getByRole("button", { name: /se connecter/i }));

  await waitFor(() => expect(login).toHaveBeenCalledWith("paul@pme-exemple.cm", "motdepasse123"));
  await waitFor(() => expect(onConnecte).toHaveBeenCalled());
});

test("affiche une erreur si le mot de passe est incorrect", async () => {
  login.mockRejectedValue(new Error("Email ou mot de passe incorrect."));
  const user = userEvent.setup();
  renderConnexion();

  await user.type(screen.getByLabelText(/email/i), "paul@pme-exemple.cm");
  await user.type(screen.getByLabelText(/mot de passe/i), "mauvais");
  await user.click(screen.getByRole("button", { name: /se connecter/i }));

  expect(await screen.findByText(/incorrect/i)).toBeInTheDocument();
});

test("bascule vers le mode inscription et appelle register() puis login()", async () => {
  register.mockResolvedValue({ email: "sophie@pme-exemple.cm" });
  login.mockResolvedValue({ access_token: "jeton", token_type: "bearer" });
  const onConnecte = vi.fn();
  const user = userEvent.setup();
  renderConnexion(onConnecte);

  await user.click(screen.getByRole("button", { name: /créer un compte/i }));
  await user.type(screen.getByLabelText(/email/i), "sophie@pme-exemple.cm");
  await user.type(screen.getByLabelText(/mot de passe/i), "motdepasse123");
  await user.click(screen.getByRole("button", { name: /créer mon compte/i }));

  await waitFor(() =>
    expect(register).toHaveBeenCalledWith("sophie@pme-exemple.cm", "motdepasse123", undefined),
  );
  await waitFor(() => expect(login).toHaveBeenCalledWith("sophie@pme-exemple.cm", "motdepasse123"));
  await waitFor(() => expect(onConnecte).toHaveBeenCalled());
});

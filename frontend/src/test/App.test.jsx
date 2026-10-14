import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { App } from "../App";

const { getKpis, getCurrentUser, getToken } = vi.hoisted(() => ({
  getKpis: vi.fn(),
  getCurrentUser: vi.fn(),
  getToken: vi.fn(),
}));

vi.mock("../api/client", () => ({
  checkHealth: vi.fn().mockResolvedValue(true),
  getKpis,
  getStockAlertes: vi.fn().mockResolvedValue([]),
  getCurrentUser,
  getToken,
  setToken: vi.fn(),
  clearToken: vi.fn(),
  onSessionExpiree: vi.fn(() => () => {}),
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
}));

beforeEach(() => {
  getCurrentUser.mockResolvedValue({ email: "paul@pme-exemple.cm", nom: "Paul" });
  // BrowserRouter s'appuie sur `window.location`, qui persiste entre les
  // tests d'un même fichier dans jsdom (contrairement au DOM, qui est
  // nettoyé automatiquement) : sans ce reset, une redirection faite par un
  // test précédent (ex. vers /connexion) fausserait le point de départ du
  // test suivant.
  window.history.pushState({}, "", "/");
});

test("redirige vers /connexion quand aucun jeton n'est présent", async () => {
  getToken.mockReturnValue(null);
  render(<App />);
  expect(await screen.findByRole("button", { name: /se connecter/i })).toBeInTheDocument();
});

test("l'application se monte et affiche la navigation + le logo (session active)", async () => {
  getToken.mockReturnValue("jeton-de-test");
  getKpis.mockResolvedValue({
    nb_clients: 12,
    nb_transactions: 0,
    chiffre_affaires_total: 0,
    stock_total_unites: 0,
    solde_tresorerie_estime: 0,
  });

  render(<App />);

  const nav = screen.getByRole("navigation");
  expect(within(nav).getByRole("link", { name: /accueil/i })).toBeInTheDocument();
  expect(within(nav).getByRole("link", { name: /données/i })).toBeInTheDocument();
  expect(within(nav).getByRole("link", { name: /copilote/i })).toBeInTheDocument();

  expect(screen.getAllByAltText(/copilote ia pme|isoler le bon client/i).length).toBeGreaterThan(0);

  await waitFor(() => expect(screen.getByText(/api connectée/i)).toBeInTheDocument());
  await waitFor(() => expect(screen.getByText(/paul/i)).toBeInTheDocument());

  // Un jeu de données est chargé (nb_clients > 0) : les pages qui en
  // dépendent doivent devenir des liens cliquables, avec le décompte
  // affiché dans le pied de la barre latérale.
  await waitFor(() =>
    expect(within(nav).getByRole("link", { name: /segmentation rfm/i })).toBeInTheDocument(),
  );
  expect(within(nav).getByRole("link", { name: /ventes & stocks/i })).toBeInTheDocument();
  expect(within(nav).getByRole("link", { name: /rapport/i })).toBeInTheDocument();
  expect(screen.getByText(/12 clients chargés/i)).toBeInTheDocument();
});

test("désactive les pages qui dépendent des données tant qu'aucun fichier n'est importé", async () => {
  getToken.mockReturnValue("jeton-de-test");
  getKpis.mockResolvedValue({
    nb_clients: 0,
    nb_transactions: 0,
    chiffre_affaires_total: 0,
    stock_total_unites: 0,
    solde_tresorerie_estime: 0,
  });

  render(<App />);

  const nav = screen.getByRole("navigation");

  // Accueil, Données et Copilote restent toujours accessibles.
  await waitFor(() => expect(screen.getByText(/aucune donnée chargée/i)).toBeInTheDocument());
  expect(within(nav).getByRole("link", { name: /données/i })).toBeInTheDocument();
  expect(within(nav).getByRole("link", { name: /copilote/i })).toBeInTheDocument();

  // Segmentation, Ventes & Stocks et Rapport sont grisées : pas de lien,
  // juste un libellé non cliquable.
  expect(within(nav).queryByRole("link", { name: /segmentation rfm/i })).not.toBeInTheDocument();
  expect(within(nav).getByText(/segmentation rfm/i)).toBeInTheDocument();
  expect(within(nav).queryByRole("link", { name: /ventes & stocks/i })).not.toBeInTheDocument();
  expect(within(nav).queryByRole("link", { name: /^rapport$/i })).not.toBeInTheDocument();
});

test("le bouton Déconnexion renvoie vers la page de connexion", async () => {
  getToken.mockReturnValue("jeton-de-test");
  getKpis.mockResolvedValue({
    nb_clients: 0,
    nb_transactions: 0,
    chiffre_affaires_total: 0,
    stock_total_unites: 0,
    solde_tresorerie_estime: 0,
  });

  const user = userEvent.setup();
  render(<App />);

  const boutonDeconnexion = await screen.findByRole("button", { name: /déconnexion/i });
  await user.click(boutonDeconnexion);

  expect(await screen.findByRole("button", { name: /se connecter/i })).toBeInTheDocument();
});

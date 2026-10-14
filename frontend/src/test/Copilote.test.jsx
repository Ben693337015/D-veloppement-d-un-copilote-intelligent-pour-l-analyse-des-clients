import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { lireFournisseurLlm, poserQuestion } from "../api/client";
import { Copilote } from "../pages/Copilote";

vi.mock("../api/client", () => ({
  poserQuestion: vi.fn(),
  lireFournisseurLlm: vi.fn().mockResolvedValue({ configure: false, avertissements: [] }),
}));

async function poser(question) {
  const user = userEvent.setup();
  render(<Copilote />);
  await user.type(screen.getByPlaceholderText(/posez votre question/i), question);
  await user.click(screen.getByRole("button", { name: /envoyer/i }));
}

test("envoie une question et affiche la réponse du copilote", async () => {
  poserQuestion.mockResolvedValueOnce({
    reponse: "CA total: 13350.71\nClients: 30",
    sources: ["analytics_service"],
    explicabilite: [],
    mode: "local",
  });
  await poser("Quel est mon chiffre d'affaires ?");
  expect(await screen.findByText(/quel est mon chiffre d'affaires/i)).toBeInTheDocument();
  expect(await screen.findByText(/ca total: 13350\.71/i)).toBeInTheDocument();
  expect(screen.getByTestId("origine-reponse")).toHaveTextContent(/aucun LLM configuré/i);
});

test("indique le fournisseur et le modèle quand la réponse vient du LLM", async () => {
  poserQuestion.mockResolvedValueOnce({
    reponse: "Votre CA est de 29,4 M.",
    mode: "llm",
    fournisseur: "openai",
    modele: "gpt-4o-mini",
  });
  await poser("Mon CA ?");
  expect(await screen.findByText(/votre ca est de 29,4 m/i)).toBeInTheDocument();
  expect(screen.getByTestId("origine-reponse")).toHaveTextContent("Réponse rédigée par le LLM · openai (gpt-4o-mini)");
});

test("signale la synthèse de secours quand le LLM est en échec", async () => {
  poserQuestion.mockResolvedValueOnce({ reponse: "Synthèse…", mode: "secours", fournisseur: "openai" });
  await poser("Mon CA ?");
  expect(await screen.findByTestId("origine-reponse")).toHaveTextContent(/LLM indisponible/i);
});

test("affiche la requête SQL exécutée quand le copilote a lu la base", async () => {
  poserQuestion.mockResolvedValueOnce({
    reponse: "Vos 3 meilleurs pays : …",
    mode: "llm",
    fournisseur: "google",
    modele: "gemini-3.6-flash",
    requete_sql: "SELECT pays, SUM(quantite * prix_unitaire) AS ca FROM transactions GROUP BY pays",
    lignes_base: 3,
  });
  await poser("CA par pays ?");
  const bloc = await screen.findByTestId("requete-sql");
  expect(bloc).toHaveTextContent("Données lues dans la base (3 lignes)");
  expect(bloc).toHaveTextContent("GROUP BY pays");
});

test("n'affiche pas de requête quand la base n'a pas été interrogée", async () => {
  poserQuestion.mockResolvedValueOnce({ reponse: "Bonjour !", mode: "llm", fournisseur: "openai" });
  await poser("Bonjour");
  await screen.findByText("Bonjour !");
  expect(screen.queryByTestId("requete-sql")).not.toBeInTheDocument();
});

test("affiche toutes les requêtes quand le copilote en a exécuté plusieurs", async () => {
  poserQuestion.mockResolvedValueOnce({
    reponse: "Ventes et stock du produit P1 : …",
    mode: "llm",
    fournisseur: "google",
    requete_sql: "SELECT SUM(quantite) AS vendu FROM transactions WHERE code_produit = 'P1'",
    requetes_sql: [
      "SELECT SUM(quantite) AS vendu FROM transactions WHERE code_produit = 'P1'",
      "SELECT quantite_disponible FROM stocks_produits WHERE code_produit = 'P1'",
    ],
    lignes_base: 2,
  });
  await poser("Ventes et stock de P1 ?");
  const bloc = await screen.findByTestId("requete-sql");
  expect(bloc).toHaveTextContent("Données lues dans la base (2 lignes, 2 requêtes)");
  expect(bloc).toHaveTextContent("FROM transactions");
  expect(bloc).toHaveTextContent("FROM stocks_produits");
});

test("affiche le fournisseur retenu d'après le .env et ses avertissements", async () => {
  lireFournisseurLlm.mockResolvedValueOnce({
    configure: true,
    fournisseur: "openai",
    modele: "gpt-4o-mini",
    source: "OPENAI_API_KEY",
    avertissements: ["LLM_MODEL=gemini-3.5-flash-lite n'est pas un modèle openai : modèle par défaut gpt-4o-mini utilisé"],
  });
  render(<Copilote />);
  const bandeau = await screen.findByTestId("fournisseur-llm");
  expect(bandeau).toHaveTextContent(/openai · gpt-4o-mini/i);
  expect(bandeau).toHaveTextContent(/OPENAI_API_KEY/);
  expect(bandeau).toHaveTextContent(/n'est pas un modèle openai/i);
});

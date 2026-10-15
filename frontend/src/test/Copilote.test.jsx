import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { lireBriefing, lireFournisseurLlm, poserQuestion } from "../api/client";
import { Copilote } from "../pages/Copilote";

vi.mock("../api/client", () => ({
  poserQuestion: vi.fn(),
  lireFournisseurLlm: vi.fn().mockResolvedValue({ configure: false, avertissements: [] }),
  lireBriefing: vi.fn().mockResolvedValue({ points: [] }),
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

test("affiche les calculs effectués par le copilote, en clair", async () => {
  poserQuestion.mockResolvedValueOnce({
    reponse: "Commandez 320 unités du produit A.",
    mode: "llm",
    fournisseur: "openai",
    outils_utilises: [
      { nom: "simulation_ventes", libelle: "Simulation « et si »", parametres: { variation_pct: -15, horizon_jours: 30, seuil_critique: 0 }, statut: "ok" },
      { nom: "prevision_ventes", libelle: "Prévision des ventes", parametres: { horizon_jours: 999 }, statut: "erreur", erreur: "hors bornes" },
    ],
  });
  await poser("Et si mes ventes baissaient de 15 % ?");
  const outils = await screen.findByTestId("outils-utilises");
  expect(outils).toHaveTextContent("✓ Simulation « et si » (horizon : 30 jours · variation : -15 %)");
  expect(outils).toHaveTextContent("✗ Prévision des ventes (horizon : 999 jours) — calcul impossible");
});

test("un exemple de question cliquable envoie directement la question", async () => {
  poserQuestion.mockResolvedValueOnce({ reponse: "Environ 4 millions.", mode: "llm", fournisseur: "openai" });
  render(<Copilote />);
  const exemples = screen.getByTestId("exemples-questions");
  await userEvent.setup().click(within(exemples).getByRole("button", { name: /60 prochains jours/i }));
  expect(poserQuestion).toHaveBeenLastCalledWith("Combien vais-je vendre dans les 60 prochains jours ?", []);
  expect(await screen.findByText("Environ 4 millions.")).toBeInTheDocument();
  expect(screen.queryByTestId("outils-utilises")).not.toBeInTheDocument();
});


test("affiche les réponses en Markdown : tableau et gras, sans astérisques", async () => {
  poserQuestion.mockResolvedValueOnce({
    reponse: "**Impact** d'une baisse :\n\n| Élément | Avant | Après |\n|---|---|---|\n| Ventes | 65 927 | 56 038 |\n\n- Agir **vite**\n- Relancer",
    mode: "llm",
    fournisseur: "groq",
  });
  await poser("Et si ?");
  const tableau = await screen.findByRole("table");
  expect(within(tableau).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Élément", "Avant", "Après"]);
  expect(within(tableau).getByText("56 038")).toBeInTheDocument();
  expect(screen.getByText("Impact").tagName).toBe("STRONG");
  expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Agir vite", "Relancer"]);
  expect(document.body.textContent).not.toMatch(/\*\*|\|---/);
});

test("envoie les derniers échanges comme mémoire de conversation", async () => {
  poserQuestion
    .mockResolvedValueOnce({ reponse: "Environ 4 millions sur 30 jours.", mode: "llm", fournisseur: "groq" })
    .mockResolvedValueOnce({ reponse: "Environ 12 millions sur 90 jours.", mode: "llm", fournisseur: "groq" });
  const user = userEvent.setup();
  render(<Copilote />);
  const champ = screen.getByPlaceholderText(/posez votre question/i);
  await user.type(champ, "Ventes sur 30 jours ?{enter}");
  await screen.findByText("Environ 4 millions sur 30 jours.");
  await user.type(champ, "Et pour 90 jours ?{enter}");
  await screen.findByText("Environ 12 millions sur 90 jours.");
  expect(poserQuestion).toHaveBeenLastCalledWith("Et pour 90 jours ?", [
    { role: "user", contenu: "Ventes sur 30 jours ?" },
    { role: "assistant", contenu: "Environ 4 millions sur 30 jours." },
  ]);
});

test("le briefing affiche les points d'attention et permet de poser la question associée", async () => {
  lireBriefing.mockResolvedValueOnce({
    points: [{ niveau: "urgent", domaine: "stocks", titre: "2 produit(s) bientôt en rupture", detail: "Produit A, Produit B.", question: "Quels produits dois-je commander, et en quelle quantité ?" }],
  });
  poserQuestion.mockResolvedValueOnce({ reponse: "Commandez 320 unités.", mode: "llm", fournisseur: "groq" });
  render(<Copilote />);
  const briefing = await screen.findByTestId("briefing");
  expect(briefing).toHaveTextContent("1 point demande votre attention");
  expect(briefing).toHaveTextContent("Urgent2 produit(s) bientôt en rupture");
  await userEvent.setup().click(within(briefing).getByRole("button", { name: /demander au copilote/i }));
  expect(poserQuestion).toHaveBeenLastCalledWith("Quels produits dois-je commander, et en quelle quantité ?", []);
  expect(await screen.findByText("Commandez 320 unités.")).toBeInTheDocument();
});

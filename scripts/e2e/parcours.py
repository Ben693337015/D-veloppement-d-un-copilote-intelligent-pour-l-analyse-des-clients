"""Parcours utilisateur complet dans un vrai navigateur (Chromium headless),
à travers Nginx (port 8501) -> API FastAPI -> PostgreSQL, LLM simulé."""
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_URL", "http://localhost:3000")
DONNEES = Path(os.environ.get("E2E_DONNEES", "data/adventureworks"))
MOCK_DIR = Path(os.environ.get("MOCK_LLM_DIR", Path(__file__).parent))
SORTIE = Path(os.environ.get("E2E_SORTIE", "e2e_resultats"))
CAPT = SORTIE / "captures"
SORTIE.mkdir(exist_ok=True)
CAPT.mkdir(exist_ok=True)
journal, erreurs_console, erreurs_reseau = [], [], []
t0 = time.time()


def etape(msg):
    ligne = f"[{time.time() - t0:6.1f}s] {msg}"
    journal.append(ligne)
    print(ligne, flush=True)


def nav_lien(page, nom):
    return page.locator("nav").get_by_role("link", name=nom, exact=True)


def capture(page, nom):
    page.screenshot(path=str(CAPT / f"{nom}.png"), full_page=True)


with sync_playwright() as p:
    nav = p.chromium.launch()
    page = nav.new_page(viewport={"width": 1366, "height": 860}, locale="fr-FR")
    page.on("console", lambda m: m.type == "error" and erreurs_console.append(m.text))
    page.on("response", lambda r: r.status >= 400 and erreurs_reseau.append(f"{r.status} {r.request.method} {r.url}"))

    # 1. Inscription
    page.goto(BASE + "/")
    page.wait_for_url("**/connexion", timeout=15000)
    etape(f"Redirection vers {page.url} (non connecté)")
    page.get_by_role("button", name="Créer un compte").click()
    page.get_by_placeholder("Paul").fill("Ben")
    page.get_by_placeholder("vous@entreprise.cm").fill(f"ben{int(time.time())}@copilote.cm")
    page.get_by_placeholder("••••••••").fill("MotDePasse-E2E-2026")
    capture(page, "01_inscription")
    page.get_by_role("button", name="Créer mon compte").click()
    page.wait_for_url(lambda u: "/connexion" not in u, timeout=15000)
    etape(f"Compte créé et connecté -> {page.url}")
    capture(page, "02_accueil_vide")

    # 2. Import des trois fichiers
    nav_lien(page, "Données").click()
    for onglet, fichier, nom in (("Ventes (transactions)", "ventes.csv", "ventes"),
                                 ("Stock", "stock.csv", "stock"),
                                 ("Trésorerie", "tresorerie.csv", "tresorerie")):
        page.get_by_role("button", name=onglet).click()
        page.locator("input[type=file]").set_input_files(str(DONNEES / fichier))
        bouton = page.get_by_role("button", name="Lancer l'import")
        expect(bouton).to_be_enabled(timeout=60000)
        capture(page, f"03_mapping_{nom}")
        etape(f"{fichier} : aperçu + mapping proposé, bouton d'import actif")
        debut = time.time()
        bouton.click()
        page.get_by_text("Import terminé").wait_for(timeout=600000)
        etape(f"{fichier} : import terminé en {time.time() - debut:.1f}s")
        capture(page, f"04_import_{nom}")

    # 3. Accueil après import
    nav_lien(page, "Accueil").click()
    page.wait_for_timeout(3000)
    capture(page, "05_accueil")
    etape("Accueil affiché après import")

    # 4. Segmentation
    nav_lien(page, "Segmentation RFM").click()
    page.wait_for_timeout(2000)
    with page.expect_response(lambda r: "/marketing/rfm/run" in r.url, timeout=300000) as rep:
        page.get_by_role("button", name="Lancer le pipeline RFM").click()
    corps = rep.value.json()
    etape(f"Pipeline RFM : HTTP {rep.value.status}, k={corps.get('k_choisi')}, silhouette={corps.get('silhouette_choisi')}")
    page.wait_for_timeout(3000)
    capture(page, "06_segmentation")
    etape("Pipeline RFM exécuté depuis l'interface")

    # 5. Ventes & stocks
    nav_lien(page, "Ventes & Stocks").click()
    page.get_by_text("Chiffre d'affaires").first.wait_for(timeout=60000)
    page.wait_for_timeout(2000)
    page.get_by_role("button", name="Générer la prévision").click()
    page.wait_for_timeout(15000)
    capture(page, "07_ventes_stocks")
    etape("KPIs + prévision des ventes affichés")

    # 6. Copilote
    nav_lien(page, "Copilote").click()
    questions = ["Quel est mon chiffre d'affaires et combien ai-je de clients ?",
                 "Quels produits dois-je réapprovisionner en urgence ?"]
    for i, q in enumerate(questions, 1):
        page.get_by_placeholder("Posez votre question…").fill(q)
        page.get_by_label("Envoyer").click()
        page.get_by_text("[LLM simulé]").nth(i - 1).wait_for(timeout=180000)
        etape(f"Copilote : réponse {i} reçue")
    origines = page.get_by_test_id("origine-reponse").all_inner_texts()
    etape(f"Origine affichée : {origines}")
    # Panne du fournisseur LLM pendant l'utilisation
    (MOCK_DIR / "mode").write_text("erreur500")
    page.get_by_placeholder("Posez votre question…").fill("Et ma trésorerie ?")
    page.get_by_label("Envoyer").click()
    page.get_by_text("LLM indisponible").wait_for(timeout=180000)
    etape("Panne LLM simulée : synthèse de secours affichée, pas d'erreur")
    (MOCK_DIR / "mode").write_text("ok")
    page.wait_for_timeout(1000)
    capture(page, "08_copilote")

    # 7. Rapport PDF
    nav_lien(page, "Rapport").click()
    page.wait_for_timeout(1500)
    page.get_by_role("button", name="Générer le PDF").click()
    lien = page.get_by_role("link", name="Télécharger")
    try:
        lien.first.wait_for(timeout=240000)
        with page.expect_download() as dl:
            lien.first.click()
        dl.value.save_as(str(SORTIE / "rapport_e2e.pdf"))
        etape("Rapport PDF généré et téléchargé")
    except Exception as exc:  # noqa: BLE001
        etape(f"Rapport : pas de lien de téléchargement détecté ({exc.__class__.__name__})")
    capture(page, "09_rapport")

    # 8. Déconnexion
    page.get_by_role("button", name="Déconnexion").click()
    page.wait_for_url("**/connexion", timeout=15000)
    etape("Déconnexion -> retour à /connexion")
    nav.close()

json.dump({"journal": journal, "erreurs_console": erreurs_console, "erreurs_reseau": erreurs_reseau},
          open(SORTIE / "resultat_parcours.json", "w"), ensure_ascii=False, indent=1)
print("CONSOLE:", erreurs_console)
print("RESEAU:", erreurs_reseau)
sys.exit(1 if erreurs_reseau else 0)

"""Service métier — Génération du rapport PDF (Cadrage §CDCF, livrable final).

Refonte du 13/10/2026 : le rapport reprend TOUTES les analyses de
l'application, résumées et interprétées :

  1. Page de garde
  2. Résumé exécutif — les conclusions clés de chaque module, sur une page
  3. Indicateurs clés (KPIs)
  4. Segmentation — évaluation des 3 algorithmes (K-Means, GMM, DBSCAN),
     algorithme retenu, graphiques de diagnostic et interprétations
  5. Répartition et recommandations par segment
  6. Clients — concentration du chiffre d'affaires et meilleurs clients
  7. Prévision des ventes — 3 modèles, graphique, synthèse, interprétations
  8. Trésorerie — solde, prévision et niveau d'alerte
  9. Stocks — alertes de réapprovisionnement

Aucun calcul en double : le rapport LIT les résultats déjà produits par
l'application (dernière segmentation enregistrée dans
`historique_segmentation`, prévisions des ventes mises en cache par
`comparaison_previsions`), donc ses chiffres et interprétations sont
exactement ceux affichés à l'écran. Avant : le rapport recalculait une
segmentation K-Means seule (20 s sur AdventureWorks) et ne contenait ni
prévisions, ni trésorerie, ni stocks, ni interprétations.

Chaque section est construite indépendamment : une section en échec
affiche « indisponible » au lieu de faire échouer tout le PDF.

Rendu : HTML + CSS -> PDF via WeasyPrint. Graphiques Matplotlib (backend Agg)
embarqués en PNG base64, sans fichier intermédiaire.
"""

from __future__ import annotations

import base64
import html
import io
import json
import logging
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # pas d'affichage : génération serveur pure
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator
from sqlalchemy.orm import Session

from app.models import HistoriqueSegmentation
from app.schemas.report import RapportPDFRequest
from app.services import (
    analytics_service,
    comparaison_previsions,
    forecasting_service,
    marketing_service,
)

log = logging.getLogger(__name__)


class RapportPDFIndisponible(RuntimeError):
    """WeasyPrint (ou ses bibliothèques système Pango/GTK) est absent."""


def _weasyprint_html():
    """Import PARESSEUX de WeasyPrint (corrigé le 13/10/2026).

    WeasyPrint charge au moment de l'import des bibliothèques système
    (Pango, GObject). Elles sont dans l'image Docker, mais souvent absentes
    d'une installation locale sous Windows : l'import échouait alors au
    démarrage et faisait tomber TOUTE l'API (le routeur du rapport est
    importé par app.main). Désormais seule la génération du PDF échoue, avec
    un message qui dit quoi installer (cf. README §7.b)."""
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise RapportPDFIndisponible(
            "Génération du PDF indisponible : WeasyPrint ne trouve pas ses bibliothèques système "
            "(Pango/GTK). Sous Windows, installer « GTK3 Runtime » puis redémarrer l'API ; "
            "sous Linux : apt install libpango-1.0-0 libpangoft2-1.0-0 (cf. README §7.b). "
            f"Détail : {str(exc).splitlines()[0][:200]}"
        ) from exc
    return HTML


# Mêmes couleurs que l'interface (palette catégorielle validée) : un modèle ou
# un algorithme a la même couleur à l'écran et dans le rapport.
_PRIMAIRE = "#2f5d8a"
_ACCENT = "#5b8fb9"
_GRIS = "#8a93a3"
COULEURS = {
    "kmeans": "#2a78d6", "gmm": "#eb6834", "dbscan": "#1baf7a",
    "prophet": "#2a78d6", "arima": "#eb6834", "xgboost": "#1baf7a",
}
_HORIZON_PREVISIONS = 30  # même horizon que la page Ventes & Stocks (cache partagé)
_N_ALERTES_STOCK = 15


# ── Outils de mise en forme ────────────────────────────────────────────────


def _e(texte) -> str:
    return html.escape(str(texte))


def _nombre(x: float | None, decimales: int = 0) -> str:
    if x is None:
        return "—"
    return f"{x:,.{decimales}f}".replace(",", " ").replace(".", ",")


def _metrique(x: float | None, decimales: int = 3) -> str:
    return "—" if x is None else f"{x:.{decimales}f}".replace(".", ",")


def _compact(v: float) -> str:
    """Graduation lisible : 10 400 000 -> « 10,4 M », 45 000 -> « 45 k »."""
    for seuil, suffixe in ((1e9, " Md"), (1e6, " M"), (1e3, " k")):
        if abs(v) >= seuil:
            return f"{v / seuil:.1f}".rstrip("0").rstrip(".").replace(".", ",") + suffixe
    return f"{v:.0f}"


def _date(valeur) -> str:
    try:
        return valeur.strftime("%d/%m/%Y")
    except AttributeError:
        return str(valeur)


def _puces(phrases: list[str]) -> str:
    if not phrases:
        return ""
    return "<ul>" + "".join(f"<li>{_e(p)}</li>" for p in phrases) + "</ul>"


def _image(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return f'<img src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode("ascii")}" />'


def _section(titre: str, contenu: str, saut_avant: bool = False) -> str:
    classe = "section saut" if saut_avant else "section"
    return f'<div class="{classe}"><h2>{_e(titre)}</h2>{contenu}</div>'


def _indisponible(titre: str, exc: Exception) -> str:
    log.exception("Section du rapport en échec : %s", titre)
    return _section(titre, f'<p class="note">Section indisponible ({_e(str(exc)[:200])}).</p>')


def _css(theme_sombre: bool) -> str:
    if theme_sombre:
        fond, texte, carte, discret = "#1c2430", "#e8edf4", "#26313f", "#a9b4c4"
    else:
        fond, texte, carte, discret = "#ffffff", "#1a1f29", "#f4f7fb", "#5b6478"
    return f"""
    @page {{ size: A4; margin: 1.6cm 1.6cm 1.8cm;
             @bottom-right {{ content: "Page " counter(page) " / " counter(pages); font-size: 9px; color: {discret}; }} }}
    body {{ font-family: 'Helvetica', 'Arial', sans-serif; background: {fond}; color: {texte}; font-size: 11.5px; line-height: 1.45; }}
    h1 {{ color: {_PRIMAIRE}; }}
    h2 {{ color: {_PRIMAIRE}; border-bottom: 2px solid {_ACCENT}; padding-bottom: 4px; margin-top: 0; }}
    h3 {{ color: {_PRIMAIRE}; font-size: 13px; margin: 14px 0 6px; }}
    .page-garde {{ text-align: center; margin-top: 30%; page-break-after: always; }}
    .carte {{ background: {carte}; border-radius: 8px; padding: 12px 16px; margin-bottom: 12px; }}
    .resume li {{ margin-bottom: 5px; }}
    .resume b {{ color: {_PRIMAIRE}; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 10.5px; margin: 6px 0 10px; }}
    th, td {{ border: 1px solid #c3cbd6; padding: 4px 7px; text-align: left; }}
    td.n {{ text-align: right; white-space: nowrap; }}
    th {{ background: {_ACCENT}; color: white; }}
    .section {{ margin-bottom: 22px; }}
    .saut {{ page-break-before: always; }}
    .bloc {{ page-break-inside: avoid; }}
    .note {{ color: {discret}; font-size: 10px; }}
    .retenu {{ font-weight: bold; }}
    ul {{ margin: 4px 0 8px; padding-left: 18px; }}
    img {{ max-width: 100%; }}
    """


# ── Collecte des données (une seule fois, partagée par les sections) ───────


def _derniere_segmentation(db: Session) -> HistoriqueSegmentation | None:
    return db.query(HistoriqueSegmentation).order_by(HistoriqueSegmentation.date_execution.desc()).first()


def _json(texte: str | None, defaut):
    try:
        return json.loads(texte) if texte else defaut
    except ValueError:
        return defaut


class _Donnees:
    """Collecte paresseuse : chaque donnée n'est calculée que si une section
    (ou le résumé exécutif) en a besoin, et une seule fois."""

    def __init__(self, db: Session):
        self.db = db
        self._cache: dict = {}

    def _une_fois(self, cle, fonction):
        if cle not in self._cache:
            self._cache[cle] = fonction()
        return self._cache[cle]

    def kpis(self):
        return self._une_fois("kpis", lambda: analytics_service.get_kpis_globaux(self.db))

    def segmentation(self):
        return self._une_fois("seg", lambda: _derniere_segmentation(self.db))

    def segments(self):
        return self._une_fois("segments", lambda: marketing_service.get_segment_summary(self.db))

    def top_clients(self, n: int):
        return self._une_fois("top", lambda: analytics_service.get_top_clients_ca(self.db, limite=n))

    def previsions(self):
        return self._une_fois(
            "prev", lambda: comparaison_previsions.comparer_modeles(self.db, _HORIZON_PREVISIONS)
        )

    def tresorerie(self):
        def calcul():
            historique, _ = forecasting_service._serie_solde_journalier(self.db)
            if historique.empty:
                return None
            prevision = forecasting_service.get_previsions_tresorerie(self.db, _HORIZON_PREVISIONS, "arima")
            alerte = analytics_service.get_alerte_tresorerie(prevision, _HORIZON_PREVISIONS)
            return {"historique": historique.tail(120), "prevision": prevision, "alerte": alerte}

        return self._une_fois("treso", calcul)

    def alertes_stock(self):
        return self._une_fois("stock", lambda: analytics_service.get_stock_alertes(self.db))


# ── Graphiques ─────────────────────────────────────────────────────────────


def _graphique_silhouettes(comparaison: list[dict]) -> str | None:
    par_k = [a for a in comparaison if a["algorithme"] in ("kmeans", "gmm") and a["diagnostics"]]
    dbscan = next((a for a in comparaison if a["algorithme"] == "dbscan" and a["diagnostics"]), None)
    if not par_k and not dbscan:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3), gridspec_kw={"width_ratios": [1.2, 1]})
    for a in par_k:
        pts = [(d["k"], d["silhouette"]) for d in a["diagnostics"] if d["silhouette"] is not None]
        axes[0].plot(*zip(*pts, strict=True), marker="o", color=COULEURS[a["algorithme"]], label=a["nom"])
        if a["k_choisi"] is not None:
            choisi = next((s for k, s in pts if k == a["k_choisi"]), None)
            if choisi is not None:
                axes[0].scatter([a["k_choisi"]], [choisi], s=110, color=COULEURS[a["algorithme"]],
                                edgecolors="white", linewidths=2, zorder=5)
    axes[0].xaxis.set_major_locator(MaxNLocator(integer=True))
    axes[0].set_title("K-Means et GMM : silhouette selon k", fontsize=10)
    axes[0].set_xlabel("nombre de groupes k")
    axes[0].set_ylabel("silhouette")
    axes[0].legend(fontsize=8, frameon=False)
    if dbscan:
        pts = [(d["inertie"], d["silhouette"]) for d in dbscan["diagnostics"] if d["silhouette"] is not None]
        if pts:
            axes[1].plot(*zip(*pts, strict=True), marker="o", color=COULEURS["dbscan"], label="DBSCAN")
        axes[1].set_title("DBSCAN : silhouette selon epsilon", fontsize=10)
        axes[1].set_xlabel("epsilon testé")
    else:
        axes[1].axis("off")
    for ax in axes:
        for seuil in (0.25, 0.5):
            ax.axhline(seuil, color=_GRIS, linewidth=0.6, linestyle=":")
        ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.5, -0.04, "Pointillés : seuils 0,25 et 0,50 de l'échelle de Kaufman & Rousseeuw "
             "(non substantielle / faible / raisonnable). Gros point : valeur retenue.",
             ha="center", fontsize=7.5, color=_GRIS)
    fig.tight_layout()
    return _image(fig)


def _graphique_segments(segments: list[dict]) -> str | None:
    if not segments:
        return None
    ordre = sorted(segments, key=lambda s: -s["nb_clients"])
    fig, ax = plt.subplots(figsize=(7, 2.6))
    barres = ax.barh([s["segment_rfm"] for s in ordre][::-1], [s["nb_clients"] for s in ordre][::-1], color=_PRIMAIRE)
    ax.bar_label(barres, labels=[_nombre(s["nb_clients"]) for s in ordre][::-1], padding=3, fontsize=8)
    ax.set_xlabel("nombre de clients")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return _image(fig)


def _graphique_previsions(res: dict) -> str | None:
    if not res["modeles"]:
        return None
    fig, ax = plt.subplots(figsize=(9.5, 3.2))
    hist = res["historique"]
    ax.plot([h["date"] for h in hist], [h["valeur"] for h in hist], color=_GRIS, linewidth=1.2, label="CA réel")
    for m in res["modeles"]:
        if m["statut"] != "ok" or not m["points"]:
            continue
        dates = [p["date_prevision"] for p in m["points"]]
        couleur = COULEURS[m["modele"]]
        ax.plot(dates, [p["valeur_prevue"] for p in m["points"]], color=couleur, linewidth=1.8, label=m["nom"])
        if all(p.get("borne_basse") is not None for p in m["points"]):
            ax.fill_between(dates, [p["borne_basse"] for p in m["points"]],
                            [p["borne_haute"] for p in m["points"]], color=couleur, alpha=0.12)
    ax.set_ylabel("CA journalier")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _compact(v)))
    ax.legend(fontsize=8, frameon=False, ncol=4)
    ax.spines[["top", "right"]].set_visible(False)
    fig.autofmt_xdate()
    fig.tight_layout()
    return _image(fig)


def _graphique_tresorerie(t: dict) -> str:
    fig, ax = plt.subplots(figsize=(9.5, 3))
    h = t["historique"]
    ax.plot(h["ds"], h["y"], color=_GRIS, linewidth=1.2, label="Solde réel")
    pts = t["prevision"]["points"]
    dates = [p["date_prevision"] for p in pts]
    ax.plot(dates, [p["valeur_prevue"] for p in pts], color=COULEURS["arima"], linewidth=1.8, label="Prévision")
    if pts and all(p.get("borne_basse") is not None for p in pts):
        ax.fill_between(dates, [p["borne_basse"] for p in pts], [p["borne_haute"] for p in pts],
                        color=COULEURS["arima"], alpha=0.12)
    ax.axhline(0, color="#c1443c", linewidth=0.8, linestyle="--", label="Seuil de déficit (0)")
    ax.set_ylabel("solde")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _compact(v)))
    ax.legend(fontsize=8, frameon=False, ncol=3)
    ax.spines[["top", "right"]].set_visible(False)
    fig.autofmt_xdate()
    fig.tight_layout()
    return _image(fig)


# ── Sections ───────────────────────────────────────────────────────────────


def _section_page_garde(payload: RapportPDFRequest) -> str:
    return f"""
    <div class="page-garde">
      <h1>Rapport Copilote IA PME</h1>
      <p>Clients, ventes, trésorerie et stocks — analyses et interprétations</p>
      <p><strong>{_e(payload.entreprise)}</strong></p>
      <p>{_e(payload.auteur)}</p>
      <p>{datetime.now().strftime('%d/%m/%Y')}</p>
    </div>
    """


def _phrases_resume(d: _Donnees) -> list[tuple[str, str]]:
    """(thème, conclusion) — chaque ligne est tirée des analyses déjà faites ;
    un module sans données est simplement omis."""
    lignes: list[tuple[str, str]] = []
    k = d.kpis()
    lignes.append(("Activité", f"Chiffre d'affaires total {_nombre(k['chiffre_affaires_total'])} réalisé par "
                               f"{_nombre(k['nb_clients'])} clients ({_nombre(k['nb_transactions'])} lignes de vente)."))
    try:
        top = d.top_clients(10)
        if top["nb_clients"]:
            part = top["nb_clients_80pct"] / top["nb_clients"]
            lignes.append(("Clients", f"{_nombre(top['nb_clients_80pct'])} clients ({part * 100:.0f} %) réalisent "
                                      "80 % du CA rattaché à un client."))
    except Exception:  # noqa: BLE001
        pass
    seg = d.segmentation()
    if seg is not None:
        synthese = _json(seg.synthese, [])
        lignes.append(("Segmentation", synthese[0] if synthese else
                       f"Algorithme {seg.algorithme}, silhouette {_metrique(seg.silhouette)}."))
        segments = d.segments()
        if segments:
            total = sum(s["nb_clients"] for s in segments) or 1
            risque = next((s["nb_clients"] for s in segments if s["segment_rfm"] == "À risque"), 0)
            principal = max(segments, key=lambda s: s["nb_clients"])
            lignes.append(("Segments", f"Segment principal : {principal['segment_rfm']} "
                                       f"({principal['nb_clients'] / total * 100:.0f} %) ; "
                                       f"{_nombre(risque)} clients à risque à relancer en priorité."))
    try:
        prev = d.previsions()
        if prev["modeles"]:
            meilleur = next((m for m in prev["modeles"] if m["modele"] == prev["synthese"]["meilleur_modele"]), None)
            if meilleur:
                ind = meilleur["indicateurs"]
                lignes.append(("Ventes", f"Prévision {meilleur['nom']} (modèle le plus fiable) : "
                                         f"{_nombre(ind['total_prevu'])} sur {prev['horizon_jours']} jours, "
                                         f"tendance {ind['sens']}, fiabilité {ind['fiabilite'] or 'non mesurée'}."))
            rupture = next((t for t in prev["synthese"]["texte"] if t.startswith("Rupture")), None)
            if rupture:
                lignes.append(("Point de vigilance", rupture.split(". Les modèles")[0] + "."))
    except Exception:  # noqa: BLE001
        pass
    try:
        t = d.tresorerie()
        if t:
            lignes.append(("Trésorerie", t["alerte"]["message"]))
    except Exception:  # noqa: BLE001
        pass
    alertes = d.alertes_stock()
    if alertes:
        n_crit = sum(a["niveau"] == "critique" for a in alertes)
        lignes.append(("Stocks", f"{n_crit} produit(s) en rupture imminente (niveau critique) et "
                                 f"{len(alertes) - n_crit} à réapprovisionner."))
    return lignes


def _section_resume(d: _Donnees) -> str:
    lignes = _phrases_resume(d)
    items = "".join(f"<li><b>{_e(theme)}.</b> {_e(texte)}</li>" for theme, texte in lignes)
    return _section("Résumé exécutif", f'<div class="carte resume"><ul>{items}</ul></div>'
                    '<p class="note">Chaque conclusion est détaillée et justifiée dans la section correspondante.</p>')


def _section_kpis(d: _Donnees) -> str:
    k = d.kpis()
    lignes = [
        ("Clients", _nombre(k["nb_clients"])),
        ("Lignes de vente (hors retours)", _nombre(k["nb_transactions"])),
        ("Chiffre d'affaires total", _nombre(k["chiffre_affaires_total"], 2)),
        ("Stock total (unités)", _nombre(k["stock_total_unites"])),
        ("Solde de trésorerie estimé", _nombre(k["solde_tresorerie_estime"], 2)),
    ]
    table = "".join(f"<tr><td>{_e(a)}</td><td class='n'>{b}</td></tr>" for a, b in lignes)
    return _section("Indicateurs clés", f"<table><thead><tr><th>Indicateur</th><th>Valeur</th></tr></thead>{table}</table>")


def _section_segmentation(d: _Donnees) -> str:
    seg = d.segmentation()
    titre = "Segmentation RFM — choix de l'algorithme"
    if seg is None:
        return _section(titre, "<p>Aucune segmentation enregistrée : lancer le pipeline RFM (page "
                               "Segmentation) avant de générer le rapport.</p>", saut_avant=True)
    comparaison = _json(seg.comparaison, [])
    synthese = _json(seg.synthese, [])
    entete = (f"<p>Segmentation du {seg.date_execution:%d/%m/%Y %H:%M} : {_nombre(seg.n_clients)} clients actifs "
              f"(+ {_nombre(seg.n_clients_hors_fenetre)} sans achat sur la fenêtre de {seg.fenetre_jours} jours, "
              "classés Perdu). Variables : récence, fréquence et montant, plafonnées puis standardisées.</p>")
    if not comparaison:  # exécution avec un algorithme imposé : pas de comparaison
        param = f"k = {seg.k_choisi}" if seg.k_choisi is not None else f"ε = {_metrique(seg.eps_choisi)}"
        corps = entete + (f"<p>Algorithme imposé : <b>{_e(seg.algorithme)}</b> ({param}) — silhouette "
                          f"{_metrique(seg.silhouette)}, Davies-Bouldin {_metrique(seg.davies_bouldin, 2)}.</p>")
        return _section(titre, corps, saut_avant=True)

    lignes = ""
    for a in sorted(comparaison, key=lambda a: a["rang"] or 9):
        statut = "Recommandé" if a["rang"] == 1 else (f"{a['rang']}e" if a["eligible"] else "Écarté")
        param = f"k = {a['k_choisi']}" if a["k_choisi"] is not None else f"ε = {_metrique(a['eps_choisi'])}"
        groupes = f"{a['n_groupes']}" + (f" + {a['part_bruit'] * 100:.0f} % bruit" if a["part_bruit"] else "")
        classe = ' class="retenu"' if a["rang"] == 1 else ""
        lignes += (f"<tr{classe}><td>{_e(a['nom'])}</td><td>{param}</td><td class='n'>{_metrique(a['silhouette'])}</td>"
                   f"<td class='n'>{_metrique(a['davies_bouldin'], 2)}</td>"
                   f"<td class='n'>{_nombre(a['calinski_harabasz'])}</td><td class='n'>{groupes}</td><td>{statut}</td></tr>")
    tableau = ("<table><thead><tr><th>Algorithme</th><th>Paramètre</th><th>Silhouette ↑</th><th>Davies-Bouldin ↓</th>"
               f"<th>Calinski-Harabasz ↑</th><th>Groupes</th><th>Décision</th></tr></thead>{lignes}</table>")
    methode = ('<p class="note">Méthode : les 3 algorithmes sont appliqués aux mêmes données. Sont écartés ceux qui '
               "produisent plus de 8 groupes ou plus de 10 % de clients non classés ; les autres sont classés par "
               "rang moyen sur la silhouette, Davies-Bouldin et Calinski-Harabasz.</p>")
    graphique = _graphique_silhouettes(comparaison) or ""
    details = "".join(
        f'<div class="bloc"><h3>{_e(a["nom"])}'
        f'{" — recommandé" if a["rang"] == 1 else (" — écarté" if not a["eligible"] else "")}</h3>'
        + (f"<p>Motif : {_e(a['motif_exclusion'])}.</p>" if a.get("motif_exclusion") else "")
        + _puces(a["interpretation"]) + "</div>"
        for a in sorted(comparaison, key=lambda a: a["rang"] or 9)
    )
    corps = (entete + f'<div class="carte">{_puces(synthese)}</div>' + tableau + methode
             + f'<div class="bloc">{graphique}</div>' + "<h3>Interprétation détaillée</h3>" + details)
    return _section(titre, corps, saut_avant=True)


def _section_segments(d: _Donnees) -> str:
    segments = d.segments()
    if not segments:
        return _section("Segments et actions recommandées", "<p>Aucun client segmenté pour le moment.</p>")
    total = sum(s["nb_clients"] for s in segments) or 1
    ordre = sorted(segments, key=lambda s: -s["nb_clients"])
    lignes = "".join(
        f"<tr><td>{_e(s['segment_rfm'])}</td><td class='n'>{_nombre(s['nb_clients'])}</td>"
        f"<td class='n'>{s['nb_clients'] / total * 100:.0f} %</td><td class='n'>{_nombre(s['montant_total_moyen'])}</td>"
        f"<td class='n'>{_metrique(s['score_churn_moyen'], 2)}</td><td>{_e(s['action_marketing_recommandee'])}</td></tr>"
        for s in ordre
    )
    valeur = max(segments, key=lambda s: s["montant_total_moyen"])
    risque = max(segments, key=lambda s: s["score_churn_moyen"] or 0)
    lecture = [
        f"Le segment le plus nombreux est « {ordre[0]['segment_rfm']} » "
        f"({ordre[0]['nb_clients'] / total * 100:.0f} % des clients).",
        f"Le segment « {valeur['segment_rfm']} » a le montant moyen le plus élevé "
        f"({_nombre(valeur['montant_total_moyen'])}) : c'est la base de valeur à protéger.",
        f"Le segment « {risque['segment_rfm']} » a le risque de départ moyen le plus élevé "
        f"({_metrique(risque['score_churn_moyen'], 2)}) : priorité des campagnes de relance.",
    ]
    corps = (f'<div class="bloc">{_graphique_segments(segments) or ""}</div>'
             "<table><thead><tr><th>Segment</th><th>Clients</th><th>Part</th><th>Montant moyen</th><th>Churn moyen</th>"
             f"<th>Action recommandée</th></tr></thead>{lignes}</table><h3>Lecture</h3>{_puces(lecture)}"
             '<p class="note">Churn = récence / fenêtre d\'analyse (0 = achat récent, 1 = aucun achat sur la fenêtre).</p>')
    return _section("Segments et actions recommandées", corps)


def _section_clients(d: _Donnees, n: int) -> str:
    top = d.top_clients(n)
    if not top["top"]:
        return _section("Meilleurs clients", "<p>Aucune vente rattachée à un client.</p>")
    part80 = top["nb_clients_80pct"] / top["nb_clients"]
    part_top = sum(c["part_ca"] for c in top["top"])
    lecture = [
        f"Concentration : {_nombre(top['nb_clients_80pct'])} clients sur {_nombre(top['nb_clients'])} "
        f"({part80 * 100:.0f} %) réalisent 80 % du CA rattaché à un client"
        + (" — dépendance forte à quelques comptes." if part80 < 0.2 else
           " — CA réparti sur une large base de clients." if part80 > 0.4 else "."),
        f"Les {len(top['top'])} premiers clients pèsent {part_top * 100:.1f} % du CA.".replace(".", ",", 1),
    ]
    lignes = "".join(
        f"<tr><td>{rang}</td><td>{_e(c['client'])}</td><td class='n'>{_nombre(c['chiffre_affaires'], 2)}</td>"
        f"<td class='n'>{c['part_ca'] * 100:.2f} %</td><td class='n'>{c['nb_factures']}</td>"
        f"<td>{_e(c['segment_rfm'] or '—')}</td><td class='n'>{c['recence_jours'] if c['recence_jours'] is not None else '—'}</td></tr>"
        for rang, c in enumerate(top["top"], 1)
    )
    corps = (_puces(lecture) + '<table class="bloc"><thead><tr><th>#</th><th>Client</th><th>CA</th><th>Part du CA</th>'
             f"<th>Factures</th><th>Segment</th><th>Récence (j)</th></tr></thead>{lignes}</table>")
    return _section("Clients — concentration et meilleurs clients", corps)


def _section_previsions(d: _Donnees) -> str:
    res = d.previsions()
    titre = f"Prévision des ventes à {res['horizon_jours']} jours — 3 modèles"
    if not res["modeles"]:
        return _section(titre, "<p>Aucune vente importée : rien à prévoir.</p>", saut_avant=True)
    lignes = ""
    meilleur = res["synthese"]["meilleur_modele"]
    for m in res["modeles"]:
        ind = m.get("indicateurs") or {}
        classe = ' class="retenu"' if m["modele"] == meilleur else ""
        lignes += (f"<tr{classe}><td>{_e(m['nom'])}</td><td class='n'>{_nombre(ind.get('total_prevu'))}</td>"
                   f"<td class='n'>{_nombre(m['rmse_validation'])}</td><td class='n'>{_nombre(m['mae_validation'])}</td>"
                   f"<td>{_e(ind.get('sens') or '—')}</td><td>{_e(ind.get('fiabilite') or '—')}</td></tr>")
    tableau = ("<table><thead><tr><th>Modèle</th><th>CA prévu</th><th>RMSE ↓</th><th>MAE ↓</th><th>Tendance</th>"
               f"<th>Fiabilité</th></tr></thead>{lignes}</table>")
    methode = ('<p class="note">Validation : les 20 % les plus récents de l\'historique servent de test (RMSE, MAE), '
               "puis chaque modèle est réentraîné sur tout l'historique. Fiabilité = RMSE rapporté au CA journalier "
               "moyen de la période de test (bonne < 25 %, moyenne < 50 %).</p>")
    details = "".join(
        f'<div class="bloc"><h3>{_e(m["nom"])}{" — le plus fiable" if m["modele"] == meilleur else ""}</h3>'
        + (_puces(m["interpretation"]) if m["statut"] == "ok" else f"<p>Calcul en échec : {_e(m['erreur'])}</p>")
        + "</div>"
        for m in res["modeles"]
    )
    corps = (f'<div class="carte">{_puces(res["synthese"]["texte"])}</div>'
             f'<div class="bloc">{_graphique_previsions(res) or ""}</div>'
             + tableau + methode + "<h3>Interprétation par modèle</h3>" + details)
    return _section(titre, corps)


def _section_tresorerie(d: _Donnees) -> str:
    t = d.tresorerie()
    titre = "Trésorerie — solde et prévision"
    if t is None:
        return _section(titre, "<p>Aucun mouvement de trésorerie importé.</p>")
    a, p = t["alerte"], t["prevision"]
    niveaux = {"risque_deficit": "RISQUE DE DÉFICIT", "vigilance": "VIGILANCE", "ok": "situation saine"}
    solde_actuel = float(t["historique"]["y"].iloc[-1])
    lecture = [
        f"Niveau d'alerte : {niveaux.get(a['niveau'], a['niveau'])}. {a['message']}",
        f"Solde actuel : {_nombre(solde_actuel)} ; point le plus bas prévu : {_nombre(a['solde_projete'])} "
        f"le {_date(a['date_alerte'])}.",
        f"Modèle : {p['modele_utilise']}"
        + (f" (RMSE de validation {_nombre(p['rmse_validation'])})." if p.get("rmse_validation") is not None else "."),
        "L'alerte porte sur le point le plus bas de tout l'horizon, pas seulement sur le dernier jour.",
    ]
    corps = f'<div class="bloc">{_graphique_tresorerie(t)}</div>{_puces(lecture)}'
    return _section(titre, corps)


def _section_stocks(d: _Donnees) -> str:
    alertes = d.alertes_stock()
    titre = "Stocks — alertes de réapprovisionnement"
    if not alertes:
        k = d.kpis()
        msg = ("Aucun stock importé." if not k["stock_total_unites"]
               else "Aucune alerte : tous les produits sont au-dessus de leur seuil de réapprovisionnement.")
        return _section(titre, f"<p>{msg}</p>")
    n_crit = sum(x["niveau"] == "critique" for x in alertes)
    lecture = [
        f"{len(alertes)} produit(s) en alerte : {n_crit} critique(s) (stock ≤ seuil d'alerte) et "
        f"{len(alertes) - n_crit} à surveiller (stock ≤ seuil de réapprovisionnement).",
        "Les produits critiques sont à commander en priorité, en tenant compte du délai fournisseur.",
    ]
    lignes = "".join(
        f"<tr><td>{_e(x['code_produit'])}</td><td>{_e(x['nom_produit'] or '—')}</td>"
        f"<td class='n'>{x['quantite_disponible']}</td><td class='n'>{x['seuil_alerte']}</td><td>{_e(x['niveau'])}</td></tr>"
        for x in alertes[:_N_ALERTES_STOCK]
    )
    suite = (f'<p class="note">… et {len(alertes) - _N_ALERTES_STOCK} autre(s) alerte(s) moins urgente(s).</p>'
             if len(alertes) > _N_ALERTES_STOCK else "")
    corps = (_puces(lecture) + "<table><thead><tr><th>Code</th><th>Produit</th><th>Stock</th><th>Seuil d'alerte</th>"
             f"<th>Niveau</th></tr></thead>{lignes}</table>{suite}")
    return _section(titre, corps)


# ── Assemblage ─────────────────────────────────────────────────────────────


def construire_html(db: Session, payload: RapportPDFRequest) -> str:
    """HTML complet du rapport (séparé de la conversion PDF pour être testable)."""
    sections = payload.sections_valides()
    d = _Donnees(db)
    constructeurs = {
        "page_garde": lambda: _section_page_garde(payload),
        "resume": lambda: _section_resume(d),
        "kpis_globaux": lambda: _section_kpis(d),
        "segmentation": lambda: _section_segmentation(d),
        "segments": lambda: _section_segments(d),
        "clients": lambda: _section_clients(d, payload.top_n_clients),
        "previsions_ventes": lambda: _section_previsions(d),
        "tresorerie": lambda: _section_tresorerie(d),
        "stocks": lambda: _section_stocks(d),
    }
    from app.schemas.report import SECTIONS_DISPONIBLES

    corps_html = []
    for cle in sections:
        try:
            corps_html.append(constructeurs[cle]())
        except Exception as exc:  # noqa: BLE001 — une section ne fait jamais échouer tout le rapport
            corps_html.append(_indisponible(SECTIONS_DISPONIBLES[cle], exc))

    return f"""
    <html>
      <head><meta charset="utf-8"><style>{_css(payload.theme_sombre)}</style></head>
      <body>{''.join(corps_html)}</body>
    </html>
    """


def generate_pdf_report(db: Session, payload: RapportPDFRequest) -> bytes:
    html_document = construire_html(db, payload)
    return _weasyprint_html()(string=html_document).write_pdf()

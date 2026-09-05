"""Service métier — Génération du rapport PDF (Cadrage §CDCF, livrable final).

Reproduit l'écran "Génération du Rapport PDF" fourni en exemple : métadonnées
(entreprise/auteur), sections à cocher, thème clair/sombre. Réutilise
INTÉGRALEMENT les pipelines déjà validés (analytics_service, marketing_service,
rfm_clustering_service) plutôt que de recalculer quoi que ce soit en double.

Le graphique "Coude K-Means" (inertie + silhouette par k) est recalculé en
`dry_run=True` : ça n'écrase jamais les segments déjà validés en base, mais
donne un diagnostic à jour même si le dataset a été enrichi depuis le dernier
`POST /marketing/rfm/run` (cf. rfm_clustering_service.run_full_pipeline).

Rendu : HTML + CSS -> PDF via WeasyPrint (cf. mémoire du projet : outil déjà
retenu pour la génération de documents). Les graphiques (coude/silhouette,
répartition des segments) sont rendus par Matplotlib (backend Agg, sans
affichage) et embarqués en PNG base64 dans le HTML — pas de fichier
intermédiaire sur disque.
"""

from __future__ import annotations

import base64
import html
import io
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # pas d'affichage : génération serveur pure
import matplotlib.pyplot as plt
from sqlalchemy.orm import Session
from weasyprint import HTML

from app.models import Client
from app.schemas.report import RapportPDFRequest
from app.services import analytics_service, marketing_service, rfm_clustering_service

_COULEUR_PRIMAIRE = "#2f5d8a"
_COULEUR_ACCENT = "#5b8fb9"


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _chart_coude_silhouette(diagnostics) -> str | None:
    """Reproduit l'écran "Graphiques" -> "Coude K-Means" (2 sous-graphiques)."""
    if not diagnostics:
        return None
    ks = [d.k for d in diagnostics]
    inerties = [d.inertie for d in diagnostics]
    silhouettes = [d.silhouette for d in diagnostics]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.2))
    ax1.plot(ks, inerties, marker="o", color=_COULEUR_PRIMAIRE)
    ax1.set_title("Méthode du coude (inertie)")
    ax1.set_xlabel("Nombre de clusters k")
    ax1.set_ylabel("Inertie")

    ax2.plot(ks, silhouettes, marker="s", color=_COULEUR_ACCENT)
    ax2.set_title("Score silhouette")
    ax2.set_xlabel("Nombre de clusters k")
    ax2.set_ylabel("Silhouette")
    ax2.set_ylim(bottom=0)

    fig.tight_layout()
    return _fig_to_base64(fig)


def _chart_distribution_segments(segments_summary: list[dict]) -> str | None:
    if not segments_summary:
        return None
    labels = [s["segment_rfm"] for s in segments_summary]
    valeurs = [s["nb_clients"] for s in segments_summary]

    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.bar(labels, valeurs, color=_COULEUR_PRIMAIRE)
    ax.set_ylabel("Nombre de clients")
    ax.set_title("Répartition des clients par segment RFM")
    fig.tight_layout()
    return _fig_to_base64(fig)


def _top_clients(db: Session, limit: int) -> list[Client]:
    return (
        db.query(Client)
        .filter(Client.segment_rfm.is_not(None))
        .order_by(Client.montant_total.desc().nulls_last())
        .limit(limit)
        .all()
    )


def _css(theme_sombre: bool) -> str:
    if theme_sombre:
        fond, texte, carte = "#1c2430", "#e8edf4", "#26313f"
    else:
        fond, texte, carte = "#ffffff", "#1a1f29", "#f4f7fb"
    return f"""
    @page {{ size: A4; margin: 2cm; }}
    body {{ font-family: 'Helvetica', 'Arial', sans-serif; background: {fond}; color: {texte}; }}
    h1 {{ color: {_COULEUR_PRIMAIRE}; }}
    h2 {{ color: {_COULEUR_PRIMAIRE}; border-bottom: 2px solid {_COULEUR_ACCENT}; padding-bottom: 4px; }}
    .page-garde {{ text-align: center; margin-top: 30%; }}
    .carte {{ background: {carte}; border-radius: 8px; padding: 14px 18px; margin-bottom: 14px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ border: 1px solid #9aa7b8; padding: 4px 8px; text-align: left; }}
    th {{ background: {_COULEUR_ACCENT}; color: white; }}
    .section {{ page-break-inside: avoid; margin-bottom: 24px; }}
    img {{ max-width: 100%; }}
    """


def _section_page_garde(payload: RapportPDFRequest) -> str:
    return f"""
    <div class="page-garde">
      <h1>Rapport Copilote IA PME</h1>
      <p>Segmentation clients &amp; performance commerciale</p>
      <p><strong>{html.escape(payload.entreprise)}</strong></p>
      <p>{html.escape(payload.auteur)}</p>
      <p>{datetime.now().strftime('%d/%m/%Y')}</p>
    </div>
    """


def _section_kpis(kpis: dict) -> str:
    return f"""
    <div class="section">
      <h2>KPIs globaux</h2>
      <div class="carte">
        <table>
          <tr><th>Indicateur</th><th>Valeur</th></tr>
          <tr><td>Clients</td><td>{kpis['nb_clients']}</td></tr>
          <tr><td>Transactions</td><td>{kpis['nb_transactions']}</td></tr>
          <tr><td>Chiffre d'affaires total</td><td>{kpis['chiffre_affaires_total']:.2f}</td></tr>
          <tr><td>Stock total (unités)</td><td>{kpis['stock_total_unites']}</td></tr>
          <tr><td>Solde trésorerie estimé</td><td>{kpis['solde_tresorerie_estime']:.2f}</td></tr>
        </table>
      </div>
    </div>
    """


def _section_coude(diagnostics, k_choisi, silhouette_choisi) -> str:
    image = _chart_coude_silhouette(diagnostics)
    corps = (
        f'<img src="data:image/png;base64,{image}" />'
        if image
        else "<p>Pas assez de données pour un diagnostic K-Means.</p>"
    )
    resume = (
        f"<p><strong>k choisi : {k_choisi}</strong> (score silhouette = {silhouette_choisi:.3f})</p>"
        if k_choisi is not None
        else ""
    )
    return f"""
    <div class="section">
      <h2>Diagnostic K-Means (coude &amp; silhouette)</h2>
      <div class="carte">{resume}{corps}</div>
    </div>
    """


def _section_distribution(segments_summary: list[dict]) -> str:
    image = _chart_distribution_segments(segments_summary)
    corps = (
        f'<img src="data:image/png;base64,{image}" />'
        if image
        else "<p>Aucun client segmenté pour le moment.</p>"
    )
    return f"""
    <div class="section">
      <h2>Distribution des segments RFM</h2>
      <div class="carte">{corps}</div>
    </div>
    """


def _section_segments_summary(segments_summary: list[dict]) -> str:
    def _ligne(s: dict) -> str:
        churn = "" if s["score_churn_moyen"] is None else f"{s['score_churn_moyen']:.3f}"
        return (
            f"<tr><td>{html.escape(s['segment_rfm'])}</td><td>{s['nb_clients']}</td>"
            f"<td>{s['montant_total_moyen']:.2f}</td><td>{churn}</td>"
            f"<td>{html.escape(s['action_marketing_recommandee'])}</td></tr>"
        )

    lignes = "".join(_ligne(s) for s in segments_summary)
    return f"""
    <div class="section">
      <h2>Répartition &amp; recommandations par segment</h2>
      <div class="carte">
        <table>
          <tr><th>Segment</th><th>Clients</th><th>Montant moyen</th><th>Churn moyen</th><th>Action recommandée</th></tr>
          {lignes or '<tr><td colspan="5">Aucune donnée</td></tr>'}
        </table>
      </div>
    </div>
    """


def _section_top_clients(clients: list[Client]) -> str:
    lignes = "".join(
        f"<tr><td>{html.escape(c.code_client_externe or str(c.client_id))}</td>"
        f"<td>{html.escape(c.segment_rfm or '-')}</td>"
        f"<td>{c.montant_total:.2f}</td>"
        f"<td>{c.recence_jours if c.recence_jours is not None else '-'}</td>"
        f"<td>{c.frequence_achats if c.frequence_achats is not None else '-'}</td></tr>"
        for c in clients
    )
    return f"""
    <div class="section">
      <h2>Extrait des meilleurs clients</h2>
      <div class="carte">
        <table>
          <tr><th>Client</th><th>Segment</th><th>Montant total</th><th>Récence (j)</th><th>Fréquence</th></tr>
          {lignes or '<tr><td colspan="5">Aucun client segmenté</td></tr>'}
        </table>
      </div>
    </div>
    """


def generate_pdf_report(db: Session, payload: RapportPDFRequest) -> bytes:
    sections = payload.sections_valides()
    corps_html: list[str] = []

    if "page_garde" in sections:
        corps_html.append(_section_page_garde(payload))

    if "kpis_globaux" in sections:
        corps_html.append(_section_kpis(analytics_service.get_kpis_globaux(db)))

    if "coude_silhouette" in sections:
        diag = rfm_clustering_service.run_full_pipeline(db, dry_run=True)
        corps_html.append(_section_coude(diag.diagnostics, diag.k_choisi, diag.silhouette_choisi))

    segments_summary = None
    if {"distribution_segments", "segments_summary"} & set(sections):
        segments_summary = marketing_service.get_segment_summary(db)

    if "distribution_segments" in sections:
        corps_html.append(_section_distribution(segments_summary or []))

    if "segments_summary" in sections:
        corps_html.append(_section_segments_summary(segments_summary or []))

    if "top_clients" in sections:
        corps_html.append(_section_top_clients(_top_clients(db, payload.top_n_clients)))

    html_document = f"""
    <html>
      <head><meta charset="utf-8"><style>{_css(payload.theme_sombre)}</style></head>
      <body>{''.join(corps_html)}</body>
    </html>
    """
    return HTML(string=html_document).write_pdf()

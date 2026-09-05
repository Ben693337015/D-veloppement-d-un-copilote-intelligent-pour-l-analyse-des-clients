/**
 * Client HTTP vers l'API Copilote IA PME — point d'entrée UNIQUE du
 * frontend vers le backend.
 *
 * Principe d'architecture (cf. README backend) : le frontend ne doit JAMAIS
 * accéder à PostgreSQL directement, uniquement via l'API FastAPI. Tous les
 * composants passent donc par ce module — aucun `fetch`/`axios` ailleurs.
 *
 * Résolution de l'URL de base, par ordre de priorité :
 *   1. window.__APP_CONFIG__.API_BASE_URL — injecté au démarrage du
 *      conteneur Docker (cf. docker/entrypoint.sh), permet de changer
 *      l'URL sans reconstruire l'image (config RUNTIME, pas build-time).
 *   2. import.meta.env.VITE_API_BASE_URL — utile en développement local
 *      (`npm run dev`) via un fichier .env.
 *   3. Valeur par défaut : http://localhost:8000/api/v1
 */
import axios from "axios";

function resolveApiBaseUrl() {
  const runtimeValue = window.__APP_CONFIG__?.API_BASE_URL;
  // Le template public/config.js contient littéralement "${API_BASE_URL}"
  // tant que l'entrypoint Docker ne l'a pas substitué (ex. en dev local
  // sans conteneur) — dans ce cas, on l'ignore et on retombe sur .env.
  if (runtimeValue && !runtimeValue.includes("${")) {
    return runtimeValue;
  }
  // Par défaut : chemin RELATIF, pas une URL absolue. Le bundle React
  // s'exécute dans le NAVIGATEUR de l'utilisateur, hors du réseau Docker
  // interne — contrairement à l'ancien dashboard Streamlit (dont le code
  // Python tournait côté serveur, dans le conteneur), il ne peut jamais
  // résoudre un nom d'hôte comme "api" (DNS interne Docker Compose,
  // invisible depuis l'extérieur). "/api/v1" fonctionne dans tous les cas
  // car Nginx (prod, cf. frontend/nginx.conf) et le serveur de dev Vite
  // (cf. vite.config.js -> server.proxy) redirigent tous deux ce chemin
  // vers le service API — le navigateur ne voit qu'une seule origine.
  return import.meta.env.VITE_API_BASE_URL || "/api/v1";
}

export const API_BASE_URL = resolveApiBaseUrl();

const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120_000, // Prophet/K-Means peuvent prendre plusieurs secondes
});

// ── AUTHENTIFICATION (Phase 4) ───────────────────────────────────────────
// Jeton JWT stocké en localStorage (persiste entre onglets/rechargements —
// acceptable pour cet outil interne à une petite équipe ; un site avec des
// enjeux de sécurité plus élevés préférerait un cookie httpOnly, hors
// périmètre "simple" choisi pour cette itération).
const CLE_STOCKAGE_TOKEN = "copilote_pme_token";

export function getToken() {
  return localStorage.getItem(CLE_STOCKAGE_TOKEN);
}

export function setToken(token) {
  localStorage.setItem(CLE_STOCKAGE_TOKEN, token);
}

export function clearToken() {
  localStorage.removeItem(CLE_STOCKAGE_TOKEN);
}

// Un seul écouteur possible par événement custom -> une petite liste
// d'abonnés suffit ; évite de tirer une dépendance de gestion d'état
// globale (Redux/Zustand) juste pour propager "session expirée".
const abonnesSessionExpiree = new Set();
export function onSessionExpiree(callback) {
  abonnesSessionExpiree.add(callback);
  return () => abonnesSessionExpiree.delete(callback);
}

http.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export async function login(email, motDePasse) {
  // L'endpoint /auth/login attend du form-urlencoded (OAuth2PasswordRequestForm
  // côté FastAPI), PAS du JSON — contrairement à tous les autres endpoints.
  const corps = new URLSearchParams();
  corps.set("username", email);
  corps.set("password", motDePasse);
  const { data } = await http.post("/auth/login", corps, {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  setToken(data.access_token);
  return data;
}

export async function register(email, motDePasse, nom) {
  const { data } = await http.post("/auth/register", { email, mot_de_passe: motDePasse, nom });
  return data;
}

export async function getCurrentUser() {
  const { data } = await http.get("/auth/me");
  return data;
}

export function logout() {
  clearToken();
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      const detail = error.response.data?.detail || error.response.statusText;
      const message = Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join(" · ")
        : String(detail);
      // 401 sur une route protégée (autre que /auth/login lui-même, où un
      // 401 signifie juste "mauvais mot de passe", pas une session expirée) :
      // le jeton est invalide/expiré -> on le purge et on prévient les
      // abonnés (App.jsx redirige alors vers /connexion) plutôt que de
      // laisser chaque page gérer ce cas individuellement.
      if (error.response.status === 401 && !error.config?.url?.includes("/auth/login")) {
        clearToken();
        abonnesSessionExpiree.forEach((cb) => cb());
      }
      throw new ApiError(message, error.response.status);
    }
    if (error.request) {
      throw new ApiError(
        `Impossible de joindre l'API (${API_BASE_URL}). Vérifiez que le service "api" tourne.`,
        0,
      );
    }
    throw new ApiError(error.message, 0);
  },
);

// ── SANTÉ ──────────────────────────────────────────────────────────────

export async function checkHealth() {
  try {
    // `new URL(path, base)` résout correctement API_BASE_URL qu'il soit
    // relatif ("/api/v1") ou absolu ("http://hote:8000/api/v1").
    const resolved = new URL(API_BASE_URL, window.location.origin);
    await axios.get(`${resolved.origin}/health`, { timeout: 5000 });
    return true;
  } catch {
    return false;
  }
}

// ── INGESTION — upload + mapping + import (n'importe quelle PME) ────────

export async function previewDataset(file) {
  const form = new FormData();
  form.append("file", file);
  const { data } = await http.post("/ingestion/preview", form);
  return data;
}

export async function importDataset(cible, file, mapping) {
  const form = new FormData();
  form.append("file", file);
  form.append("mapping", JSON.stringify(mapping));
  const { data } = await http.post(`/ingestion/import/${cible}`, form);
  return data;
}

// ── MARKETING — segmentation RFM (module Abdoulmadjid) ──────────────────

export async function runRfmPipeline({
  algorithme = "kmeans",
  kMin = 2,
  kMax = 6,
  fenetreJours,
  winsorPercentile,
  dryRun = false,
} = {}) {
  const payload = { algorithme, k_min: kMin, k_max: kMax, dry_run: dryRun };
  if (fenetreJours != null) payload.fenetre_jours = fenetreJours;
  if (winsorPercentile != null) payload.winsor_percentile = winsorPercentile;
  const { data } = await http.post("/marketing/rfm/run", payload);
  return data;
}

export async function getSegmentsSummary() {
  const { data } = await http.get("/marketing/segments/summary");
  return data;
}

export async function getClients({ segment, limit = 200 } = {}) {
  const params = { limit };
  if (segment) params.segment = segment;
  const { data } = await http.get("/marketing/clients", { params });
  return data;
}

// ── ANALYTICS — ventes, stocks, trésorerie (module Maslaw) ───────────────

export async function getKpis() {
  const { data } = await http.get("/analytics/kpis");
  return data;
}

export async function getStockAlertes() {
  const { data } = await http.get("/analytics/stock/alertes");
  return data;
}

export async function getPrevisionVentes({ horizonJours = 30, modele = "prophet" } = {}) {
  const { data } = await http.get("/analytics/forecast/sales", {
    params: { horizon_jours: horizonJours, modele },
  });
  return data;
}

// ── COPILOTE ───────────────────────────────────────────────────────────

export async function poserQuestion(question, clientId) {
  const payload = { question };
  if (clientId) payload.client_id = clientId;
  const { data } = await http.post("/copilot/chat", payload);
  return data;
}

// ── RAPPORT PDF ────────────────────────────────────────────────────────

export async function genererRapportPdf({
  entreprise,
  auteur,
  themeSombre = false,
  sections,
  topNClients = 20,
}) {
  const payload = {
    entreprise,
    auteur,
    theme_sombre: themeSombre,
    top_n_clients: topNClients,
  };
  if (sections) payload.sections = sections;
  const response = await http.post("/report/pdf", payload, { responseType: "blob" });
  return response.data; // Blob
}

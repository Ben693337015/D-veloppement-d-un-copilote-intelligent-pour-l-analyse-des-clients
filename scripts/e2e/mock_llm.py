"""Faux fournisseur LLM compatible OpenAI (POST /v1/chat/completions).

- Enregistre chaque requête reçue dans requetes.jsonl (pour vérifier ce que
  l'application envoie réellement : clé, modèle, prompt système, contexte).
- Le comportement se pilote par le fichier `mode` :
  ok | erreur500 | cle_invalide | vide | malforme | lent
- En mode ok, la réponse cite des chiffres EXTRAITS du contexte reçu, pour
  prouver que le contexte de l'entreprise arrive bien jusqu'au LLM.
"""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ICI = Path(os.environ.get("MOCK_LLM_DIR", Path(__file__).parent))


def mode() -> str:
    f = ICI / "mode"
    return f.read_text().strip() if f.exists() else "ok"


def extraire(motif: str, texte: str) -> str:
    m = re.search(motif, texte)
    return m.group(1) if m else "?"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code: int, obj) -> None:
        corps = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_POST(self):
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with (ICI / "requetes.jsonl").open("a") as f:
            f.write(json.dumps({"path": self.path, "auth": self.headers.get("Authorization"),
                                "body": corps}, ensure_ascii=False) + "\n")
        m = mode()
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": "chemin inconnu"})
        if m == "erreur500":
            return self._json(500, {"error": {"message": "panne simulée du fournisseur"}})
        if m == "cle_invalide":
            return self._json(401, {"error": {"message": "Incorrect API key provided"}})
        if m == "malforme":
            return self._json(200, {"inattendu": True})
        if m == "vide":
            return self._json(200, {"choices": [{"message": {"role": "assistant", "content": ""}}]})
        if m == "lent":
            time.sleep(25)  # > TIMEOUT_SECONDES (20 s) du client
        user = corps["messages"][-1]["content"]
        question = extraire(r"Question : (.*)$", user)
        ca = extraire(r"CA total: ([0-9.]+)", user)
        clients = extraire(r"Clients: (\d+)", user)
        alertes = extraire(r"Alertes stock actives: (\d+)", user)
        tendance = extraire(r"niveau d'alerte : (\w+)", user)
        texte = (f"[LLM simulé] Réponse à « {question} » : votre CA total est de {ca} "
                 f"pour {clients} clients ; {alertes} alertes de stock sont actives ; "
                 f"niveau d'alerte trésorerie : {tendance}.")
        return self._json(200, {"id": "sim-1", "object": "chat.completion", "model": corps["model"],
                                "choices": [{"index": 0, "finish_reason": "stop",
                                             "message": {"role": "assistant", "content": texte}}]})


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_LLM_PORT", "9100"))
    print(f"LLM simulé sur http://127.0.0.1:{port}/v1 (mode : fichier {ICI / 'mode'})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()

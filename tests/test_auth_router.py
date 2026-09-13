"""Tests du parcours d'authentification JWT réel (Phase 4).

Utilise `client_sans_auth` (PAS `client`) : ce fixture n'override pas
`get_current_user`, donc ces tests exercent le VRAI chemin — hachage
bcrypt, émission/validation du JWT, protection effective des routes.
"""


def test_register_creates_a_user(client_sans_auth):
    resp = client_sans_auth.post(
        "/api/v1/auth/register",
        json={"email": "paul@pme-exemple.cm", "mot_de_passe": "motdepasse123", "nom": "Paul"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "paul@pme-exemple.cm"
    assert body["nom"] == "Paul"
    assert "mot_de_passe" not in body
    assert "mot_de_passe_hache" not in body  # jamais exposé, même haché


def test_register_rejects_duplicate_email(client_sans_auth):
    payload = {"email": "sophie@pme-exemple.cm", "mot_de_passe": "motdepasse123"}
    resp1 = client_sans_auth.post("/api/v1/auth/register", json=payload)
    assert resp1.status_code == 201

    resp2 = client_sans_auth.post("/api/v1/auth/register", json=payload)
    assert resp2.status_code == 409


def test_register_rejects_short_password(client_sans_auth):
    resp = client_sans_auth.post(
        "/api/v1/auth/register",
        json={"email": "marc@pme-exemple.cm", "mot_de_passe": "court"},
    )
    assert resp.status_code == 422


def test_register_rejects_invalid_email(client_sans_auth):
    resp = client_sans_auth.post(
        "/api/v1/auth/register",
        json={"email": "pas-un-email", "mot_de_passe": "motdepasse123"},
    )
    assert resp.status_code == 422


def test_login_with_correct_credentials_returns_a_token(client_sans_auth):
    client_sans_auth.post(
        "/api/v1/auth/register",
        json={"email": "paul@pme-exemple.cm", "mot_de_passe": "motdepasse123"},
    )
    resp = client_sans_auth.post(
        "/api/v1/auth/login",
        data={"username": "paul@pme-exemple.cm", "password": "motdepasse123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20


def test_login_with_wrong_password_is_rejected(client_sans_auth):
    client_sans_auth.post(
        "/api/v1/auth/register",
        json={"email": "paul@pme-exemple.cm", "mot_de_passe": "motdepasse123"},
    )
    resp = client_sans_auth.post(
        "/api/v1/auth/login",
        data={"username": "paul@pme-exemple.cm", "password": "mauvais-mot-de-passe"},
    )
    assert resp.status_code == 401


def test_login_with_unknown_email_is_rejected(client_sans_auth):
    resp = client_sans_auth.post(
        "/api/v1/auth/login",
        data={"username": "inconnu@pme-exemple.cm", "password": "peu-importe"},
    )
    assert resp.status_code == 401


def test_protected_endpoint_rejects_request_without_token(client_sans_auth):
    resp = client_sans_auth.get("/api/v1/analytics/kpis")
    assert resp.status_code == 401


def test_protected_endpoint_rejects_garbage_token(client_sans_auth):
    resp = client_sans_auth.get(
        "/api/v1/analytics/kpis", headers={"Authorization": "Bearer ceci-nest-pas-un-jwt"}
    )
    assert resp.status_code == 401


def test_protected_endpoint_accepts_valid_token_end_to_end(client_sans_auth):
    """Le test central : parcours COMPLET register -> login -> jeton ->
    accès effectif à une route protégée d'un autre module (analytics),
    sans aucun raccourci de test."""
    client_sans_auth.post(
        "/api/v1/auth/register",
        json={"email": "paul@pme-exemple.cm", "mot_de_passe": "motdepasse123"},
    )
    login_resp = client_sans_auth.post(
        "/api/v1/auth/login",
        data={"username": "paul@pme-exemple.cm", "password": "motdepasse123"},
    )
    token = login_resp.json()["access_token"]

    resp = client_sans_auth.get(
        "/api/v1/analytics/kpis", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    assert "nb_clients" in resp.json()


def test_me_endpoint_returns_the_authenticated_user(client_sans_auth):
    client_sans_auth.post(
        "/api/v1/auth/register",
        json={"email": "paul@pme-exemple.cm", "mot_de_passe": "motdepasse123", "nom": "Paul DG"},
    )
    token = client_sans_auth.post(
        "/api/v1/auth/login",
        data={"username": "paul@pme-exemple.cm", "password": "motdepasse123"},
    ).json()["access_token"]

    resp = client_sans_auth.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "paul@pme-exemple.cm"
    assert resp.json()["nom"] == "Paul DG"


def test_health_endpoint_stays_public(client_sans_auth):
    """`/health` (hors /api/v1) reste accessible sans jeton — utilisé par
    le frontend avant même qu'un utilisateur soit connecté."""
    resp = client_sans_auth.get("/health")
    assert resp.status_code == 200


def test_disabled_user_cannot_login(client_sans_auth, db_session):
    from app.models import User

    client_sans_auth.post(
        "/api/v1/auth/register",
        json={"email": "desactive@pme-exemple.cm", "mot_de_passe": "motdepasse123"},
    )
    utilisateur = db_session.query(User).filter(User.email == "desactive@pme-exemple.cm").first()
    utilisateur.actif = False
    db_session.commit()

    resp = client_sans_auth.post(
        "/api/v1/auth/login",
        data={"username": "desactive@pme-exemple.cm", "password": "motdepasse123"},
    )
    assert resp.status_code == 403

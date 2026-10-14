from app.core.security import (
    LONGUEUR_MAX_MOT_DE_PASSE,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_password_produces_a_different_string_each_time():
    """bcrypt génère un sel aléatoire à chaque hachage — même mot de passe,
    hash différent à chaque appel (propriété de sécurité fondamentale)."""
    h1 = hash_password("motdepasse123")
    h2 = hash_password("motdepasse123")
    assert h1 != h2
    assert verify_password("motdepasse123", h1)
    assert verify_password("motdepasse123", h2)


def test_verify_password_rejects_wrong_password():
    hache = hash_password("motdepasse123")
    assert verify_password("mauvais-mot-de-passe", hache) is False


def test_verify_password_never_raises_on_malformed_hash():
    """Un hash corrompu/mal formé en base ne doit jamais faire planter la
    vérification — retourne False plutôt que de lever une exception."""
    assert verify_password("peu-importe", "ceci-nest-pas-un-hash-bcrypt") is False


def test_hash_password_rejects_passwords_longer_than_72_bytes():
    """bcrypt tronque silencieusement au-delà de 72 octets — on préfère
    refuser explicitement plutôt que d'accepter silencieusement un mot de
    passe dont seuls les 72 premiers octets comptent réellement (cf.
    docstring module pour le contexte de cette limite)."""
    mot_de_passe_trop_long = "a" * (LONGUEUR_MAX_MOT_DE_PASSE + 1)
    try:
        hash_password(mot_de_passe_trop_long)
        raise AssertionError("Devrait avoir levé ValueError")
    except ValueError:
        pass


def test_access_token_roundtrip():
    token = create_access_token(subject="paul@pme-exemple.cm")
    assert decode_access_token(token) == "paul@pme-exemple.cm"


def test_decode_access_token_rejects_garbage():
    assert decode_access_token("ceci-nest-pas-un-jwt") is None


def test_decode_access_token_rejects_token_signed_with_different_secret():
    from jose import jwt

    token_falsifie = jwt.encode({"sub": "paul@pme-exemple.cm"}, "mauvaise-cle", algorithm="HS256")
    assert decode_access_token(token_falsifie) is None

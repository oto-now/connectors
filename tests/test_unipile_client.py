"""Tests du client Unipile (`client.UnipileClient`, API v2 — seul client).

Verrouille : (1) les paths/params/corps v2 (account_id en path, endpoints
réorganisés), (2) la normalisation d'enveloppe `data/next_cursor → items/cursor`,
(3) la **garde anti-mismatch** identifier↔réponse (feedback #153), (4) le mapping
d'erreurs réseau propre + caviardage de l'account_id (#177/#178), (5) la factory,
(6) l'enrichissement attendee des fils + `resolve_attendee_names`.

Aucun réseau : `_request` est monkeypatché pour capturer (method, path, params,
json) et rendre une réponse canned.
"""

import pytest
import requests

from oto.tools.unipile import UnipileClient, make_unipile_client
from oto.tools.unipile._api.network import _invitations_cursor
from oto.tools.unipile.client import UnipileError


def _client(canned=None, recorder=None):
    """Client avec `_request` stubé. `canned` = valeur rendue (ou callable
    `(method, path, params, json) -> value`). `recorder` = liste qui reçoit les
    tuples d'appel."""
    c = UnipileClient(api_key="k", account_id="acc")

    def fake(method, path, params=None, json=None, timeout=None):
        if recorder is not None:
            recorder.append((method, path, params, json))
        if callable(canned):
            return canned(method, path, params, json)
        return canned

    c._request = fake  # type: ignore[method-assign]
    return c


def _seq_client(responses):
    """Client stubé par SÉQUENCE : `responses` = liste de (method, path, payload)
    matchés par (method, path). Journalise les appels dans `c._calls`."""
    c = UnipileClient(api_key="k", account_id="acc")
    calls: list[dict] = []

    def fake(method, path, params=None, json=None, timeout=None):
        calls.append({"method": method, "path": path, "params": params or {}})
        for i, (m, p, payload) in enumerate(responses):
            if m == method and p == path:
                responses.pop(i)
                return payload
        raise AssertionError(f"appel inattendu : {method} {path}")

    c._request = fake  # type: ignore[method-assign]
    c._calls = calls
    return c


# ---- factory -------------------------------------------------------------

def test_factory_builds_client():
    assert isinstance(make_unipile_client(api_key="k"), UnipileClient)
    assert isinstance(make_unipile_client(api_key="k", account_id="acc"),
                      UnipileClient)


# ---- account_id dans le path --------------------------------------------

def test_account_id_in_path_and_norm():
    rec = []
    c = _client(canned={"data": [{"id": 1}], "next_cursor": "N", "total_count": 3},
                recorder=rec)
    out = c.list_chats(limit=5, cursor="C")
    method, path, params, json = rec[0]
    assert method == "GET"
    # v2 : les chats sont rangés par inbox (défaut CLASSIC_PRIMARY), account en path.
    assert path == "/acc/inboxes/CLASSIC_PRIMARY/chats"
    assert params == {"limit": 5, "cursor": "C"}
    # normalisation : items/cursor ajoutés, natifs conservés
    assert out["items"] == [{"id": 1}]
    assert out["cursor"] == "N"
    assert out["data"] == [{"id": 1}] and out["next_cursor"] == "N"


def test_list_chats_custom_inbox_and_inboxes_path():
    rec = []
    c = _client(canned={"data": []}, recorder=rec)
    c.list_chats(inbox="CLASSIC_INMAIL")
    assert rec[0][1] == "/acc/inboxes/CLASSIC_INMAIL/chats"
    rec.clear()
    c.list_inboxes()
    assert rec[0][1] == "/acc/inboxes"


# ---- découverte du compte LinkedIn (casse du provider en v2) ------------
# v2 : un compte porte `provider:"linkedin"` (MINUSCULES) et plus aucun champ `type`
# (champs relevés en live : application_id, created_at, id, is_locked, metadata, name,
# object, provider, proxy, status, user_id). Le test historique `== "LINKEDIN"` ne
# pouvait donc plus jamais être vrai → « aucun compte LinkedIn connecté » alors qu'un
# compte opérationnel existait.

def _accounts_client(accounts):
    c = UnipileClient(api_key="k")          # pas d'account_id → découverte
    c._request = lambda *a, **k: {"data": accounts}  # type: ignore[method-assign]
    return c


def test_account_id_discovers_lowercase_provider_v2():
    c = _accounts_client([
        {"id": "acc-wa", "provider": "whatsapp", "object": "Account"},
        {"id": "acc-li", "provider": "linkedin", "object": "Account"},
    ])
    assert c.account_id() == "acc-li"


def test_account_id_still_accepts_v1_uppercase_type():
    c = _accounts_client([{"id": "acc-li", "type": "LINKEDIN"}])
    assert c.account_id() == "acc-li"


def test_account_id_error_lists_connected_providers():
    # Le diagnostic ne doit pas MENTIR : il énumère ce qui est réellement connecté.
    c = _accounts_client([{"id": "acc-wa", "provider": "whatsapp"},
                          {"id": "acc-g", "provider": "google"}])
    with pytest.raises(UnipileError) as e:
        c.account_id()
    assert "whatsapp" in str(e.value) and "google" in str(e.value)


# ---- list_accounts : pagination par offset ------------------------------
#
# `GET /v2/accounts` rend 20 comptes par page, TRIÉS PAR `name`, avec `has_more`.
# Lire la seule première page rendait invisible tout compte rangé après le 20e —
# la réconciliation d'oto-backend ne pouvait alors plus lier ce compte-là dès que la
# clé plateforme dépassait 20 comptes (2026-09-14).

def _paged_accounts_client(accounts, page_size=20, honour_offset=True):
    """Client dont `GET /accounts` pagine `accounts` comme l'amont v2. Journalise les
    params dans `c._calls`. `honour_offset=False` simule un amont qui ignore `offset`."""
    c = UnipileClient(api_key="k")
    calls: list = []

    def fake(method, path, params=None, json=None, timeout=None):
        assert (method, path) == ("GET", "/accounts")
        calls.append(params)
        offset = int((params or {}).get("offset") or 0) if honour_offset else 0
        page = accounts[offset:offset + page_size]
        return {"object": "Accounts", "data": page,
                "has_more": offset + page_size < len(accounts)}

    c._request = fake  # type: ignore[method-assign]
    c._calls = calls
    return c


def _named(n):
    return [{"id": f"acc-{i:03d}", "name": f"Name {i:03d}", "provider": "linkedin"}
            for i in range(n)]


def test_list_accounts_reads_every_page():
    accounts = _named(45)
    c = _paged_accounts_client(accounts)
    assert c.list_accounts() == accounts
    assert c._calls == [None, {"offset": 20}, {"offset": 40}]


def test_list_accounts_sees_the_account_past_the_first_page():
    # Le cas vécu : 20 comptes rangés avant lui, « Zoé Exemple » est le 21e.
    accounts = _named(20) + [{"id": "acc-z", "name": "Zoé Exemple",
                              "provider": "linkedin"}]
    ids = [a["id"] for a in _paged_accounts_client(accounts).list_accounts()]
    assert "acc-z" in ids and len(ids) == 21


def test_list_accounts_single_page_is_one_call():
    c = _paged_accounts_client(_named(3))
    assert len(c.list_accounts()) == 3
    assert c._calls == [None]


def test_list_accounts_stops_when_upstream_ignores_offset():
    # Amont qui ressert la page 1 à chaque offset : on s'arrête au 2e appel.
    c = _paged_accounts_client(_named(45), honour_offset=False)
    assert len(c.list_accounts()) == 20
    assert len(c._calls) == 2


def test_list_accounts_dedupes_accounts_shifted_across_pages():
    # Un compte créé pendant le parcours décale l'ordre : l'amont ressert un compte
    # déjà vu en tête de la page suivante.
    pages = iter([
        {"data": [{"id": "a"}, {"id": "b"}], "has_more": True},
        {"data": [{"id": "b"}, {"id": "c"}], "has_more": False},
    ])
    c = UnipileClient(api_key="k")
    c._request = lambda *a, **k: next(pages)  # type: ignore[method-assign]
    assert [a["id"] for a in c.list_accounts()] == ["a", "b", "c"]


def test_list_accounts_empty_page_after_has_more_is_logged(caplog):
    pages = iter([{"data": [{"id": "a"}], "has_more": True}, {"data": [], "has_more": False}])
    c = UnipileClient(api_key="k")
    c._request = lambda *a, **k: next(pages)  # type: ignore[method-assign]
    with caplog.at_level("WARNING"):
        assert [a["id"] for a in c.list_accounts()] == ["a"]
    assert "has_more" in caplog.text


def test_list_accounts_without_has_more_is_a_single_page():
    c = _accounts_client([{"id": "x"}])
    assert c.list_accounts() == [{"id": "x"}]


def test_account_id_discovers_linkedin_on_a_later_page():
    accounts = ([{"id": f"wa-{i}", "name": f"+33 {i:02d}", "provider": "whatsapp"}
                 for i in range(20)]
                + [{"id": "acc-li", "name": "Zoé", "provider": "linkedin"}])
    assert _paged_accounts_client(accounts).account_id() == "acc-li"


# ---- profils : garde anti-mismatch (#153) -------------------------------

def test_get_profile_ok_when_identity_matches():
    c = _client(canned={"object": "UserProfile", "public_identifier": "john-doe",
                        "id": "ACoAAA"})
    out = c.get_profile("john-doe")
    assert out["public_identifier"] == "john-doe"


def test_get_profile_matches_on_provider_id():
    c = _client(canned={"object": "UserProfile", "public_identifier": "john-doe",
                        "id": "ACoAAA"})
    assert c.get_profile("ACoAAA")["id"] == "ACoAAA"  # demandé par provider id


def test_get_profile_rejects_wrong_member(recwarn=None):
    # Sous concurrence l'API a rendu un AUTRE membre → on rejette (#144-149).
    c = _client(canned={"object": "UserProfile", "public_identifier": "someone-else",
                        "id": "ACoBBB"})
    with pytest.raises(UnipileError) as e:
        c.get_profile("john-doe")
    assert "identity_mismatch" in str(e.value)


def test_get_profile_rejects_company_object():
    # Reçu un CompanyProfile à la place d'un UserProfile (#148/#149).
    c = _client(canned={"object": "CompanyProfile", "public_identifier": "john-doe"})
    with pytest.raises(UnipileError):
        c.get_profile("john-doe")


def test_get_company_rejects_user_object():
    c = _client(canned={"object": "UserProfile", "public_identifier": "acme"})
    with pytest.raises(UnipileError):
        c.get_company("acme")


# ---- résolution de slug tolérante (#176) --------------------------------

def _company_stub(known_slug, search_hits):
    """Stub `_request` : GET company connu → 200 ; GET inconnu → 404 ;
    POST search/companies → `search_hits`."""
    def fake(method, path, params=None, json=None, timeout=None):
        if method == "POST" and path.endswith("/linkedin/search/companies"):
            return {"data": search_hits}
        if method == "GET" and path.endswith(f"/linkedin/company/{known_slug}"):
            return {"object": "CompanyProfile", "public_identifier": known_slug}
        raise UnipileError("Unipile 404: Company not found", status_code=404)
    return fake


def test_get_company_falls_back_to_canonical_slug():
    # "mooniz" (marque) en 404 → search → public_identifier "mooniz1" → 200 (#176).
    c = _client()
    c._request = _company_stub("mooniz1", [{"public_identifier": "mooniz1"}])
    out = c.get_company("mooniz")
    assert out["public_identifier"] == "mooniz1"


def test_get_company_fallback_via_profile_url():
    # Candidat sans public_identifier → slug dérivé de l'URL /company/<slug>.
    c = _client()
    c._request = _company_stub(
        "mooniz1",
        [{"public_profile_url": "https://www.linkedin.com/company/mooniz1/"}],
    )
    assert c.get_company("mooniz")["public_identifier"] == "mooniz1"


def test_get_company_404_lists_candidates_when_retry_fails():
    # Search rend des candidats mais aucun ne résout → 404 propre + candidats.
    c = _client()

    def fake(method, path, params=None, json=None, timeout=None):
        if method == "POST" and path.endswith("/linkedin/search/companies"):
            return {"data": [{"public_identifier": "other-co"}]}
        raise UnipileError("Unipile 404: Company not found", status_code=404)

    c._request = fake
    with pytest.raises(UnipileError) as e:
        c.get_company("mooniz")
    assert e.value.status_code == 404
    assert "other-co" in str(e.value)


def test_get_company_numeric_id_no_fallback():
    # Un id numérique introuvable ne déclenche PAS de recherche par nom.
    calls = []
    c = _client()

    def fake(method, path, params=None, json=None, timeout=None):
        calls.append((method, path))
        raise UnipileError("Unipile 404: Company not found", status_code=404)

    c._request = fake
    with pytest.raises(UnipileError):
        c.get_company("93154483")
    assert not any("search" in p for _, p in calls)  # pas de fallback recherche


def test_get_company_resolve_false_disables_fallback():
    calls = []
    c = _client()

    def fake(method, path, params=None, json=None, timeout=None):
        calls.append(path)
        raise UnipileError("Unipile 404: Company not found", status_code=404)

    c._request = fake
    with pytest.raises(UnipileError):
        c.get_company("mooniz", resolve=False)
    assert not any("search" in p for p in calls)


def test_get_profile_with_sections_mapping():
    rec = []
    c = _client(canned={"object": "UserProfile", "public_identifier": "x", "id": "x"},
                recorder=rec)
    c.get_profile("x", sections="experience,education")
    _, path, params, _ = rec[0]
    assert path == "/acc/users/x"
    assert params["with_sections"] == ["linkedin_experience", "linkedin_education"]

    rec.clear()
    c.get_profile("x", sections="*")   # tout → pas de param
    assert "with_sections" not in (rec[0][2] or {})


def test_sections_ferme_la_liste_et_nomme_ce_qui_existe():
    """Une section inconnue partait telle quelle et revenait en `400
    querystring/with_sections/N : Expected kind 'StringEnum'` — qui ne nomme ni la
    fautive ni ce qui existe (vu le 2026-10-12 sur `experience,about`). Le client
    refuse AVANT l'appel, en nommant les sections ; les noms v1 sont traduits, et
    `about` (toujours servi, dans `bio`) se retire sans erreur."""
    from oto.tools.unipile.const import PROFILE_SECTIONS, _sections_param

    # les dix sections v2, nues ou déjà préfixées, et leur forme `_preview`
    assert _sections_param(",".join(PROFILE_SECTIONS)) == [
        f"linkedin_{s}" for s in PROFILE_SECTIONS]
    assert _sections_param("linkedin_skills, experience_preview,*_preview") == [
        "linkedin_skills", "linkedin_experience_preview", "linkedin_*_preview"]
    # `about` : rien à demander, le profil de base le porte — le reste part
    assert _sections_param("experience,about") == ["linkedin_experience"]
    assert _sections_param("about") is None
    # noms v1 → section v2, sans doublon
    assert _sections_param(
        "volunteering_experience,recommendations_received,recommendations_given"
    ) == ["linkedin_volunteer_experience", "linkedin_recommendations"]

    rec = []
    c = _client(canned={"object": "UserProfile", "public_identifier": "x", "id": "x"},
                recorder=rec)
    with pytest.raises(UnipileError) as exc:
        c.get_profile("x", sections="experience,headline")
    assert exc.value.status_code is None          # refus d'entrée, pas un 4xx amont
    assert "'headline'" in str(exc.value)
    for nom in PROFILE_SECTIONS:
        assert nom in str(exc.value)
    assert rec == []                              # rien n'est parti


def test_get_own_profile_skips_guard():
    rec = []
    c = _client(canned={"object": "UserProfile", "public_identifier": "me-real"},
                recorder=rec)
    c.get_own_profile()   # pas de mismatch même si l'id ≠ "me"
    assert rec[0][1] == "/acc/users/me"


# ---- invitations / relations --------------------------------------------

def test_list_invitations_type_param():
    rec = []
    c = _client(canned={"data": []}, recorder=rec)
    c.list_invitations(direction="sent", limit=10)
    _, path, params, _ = rec[0]
    assert path == "/acc/users/me/relation-requests"
    assert params["type"] == "sent" and params["limit"] == 10


def test_list_invitations_never_sends_cursor_upstream():
    """LA régression. `relation-requests` est paginé par offset et REFUSE tout
    param hors limit/meta_only à côté d'un `cursor` ; `type` étant obligatoire,
    toute page 2 rendait `Unipile 400: ... Unexpected parameters: type`. Le
    curseur rendu est FABRIQUÉ par nous : il se décode en offset et ne part
    jamais en amont."""
    rec = []
    c = _client(canned={"data": [{"id": "a"}]}, recorder=rec)
    c.list_invitations(direction="sent", limit=100, cursor="off:100")
    _, _, params, _ = rec[0]
    assert "cursor" not in params
    assert params["offset"] == 100
    assert params["type"] == "sent"      # toujours envoyé : param REQUIS


def test_list_invitations_mints_next_cursor():
    """Le tool annonce « Paginé » : l'amont ne rendant jamais de `next_cursor`,
    on le fabrique — sinon l'appelant n'a rien à repasser."""
    c = _client(canned={"data": [{"id": "a"}, {"id": "b"}]})
    out = c.list_invitations(direction="sent", limit=2)
    assert out["cursor"] == out["next_cursor"]
    assert _invitations_cursor(out["next_cursor"])[0] == 2
    # et il se reboucle : le curseur rendu redevient l'offset suivant
    rec = []
    c2 = _client(canned={"data": [{"id": "c"}]}, recorder=rec)
    c2.list_invitations(direction="sent", limit=2, cursor=out["next_cursor"])
    assert rec[0][2]["offset"] == 2


def test_list_invitations_empty_page_ends_pagination():
    """Fin de liste = `data` vide (contrat Unipile), PAS une page courte : sans
    curseur rendu, la boucle appelante s'arrête."""
    c = _client(canned={"data": []})
    out = c.list_invitations(direction="sent", limit=50)
    assert "next_cursor" not in out and "cursor" not in out


def test_list_invitations_short_page_still_advances_by_limit():
    """Une page courte n'est PAS la fin : le provider filtre dans la fenêtre.
    On avance de `limit` (contrat Unipile), pas de `len(data)` — sinon on
    re-sert les mêmes items."""
    c = _client(canned={"data": [{"id": "a"}] * 90})
    out = c.list_invitations(direction="sent", limit=100)
    assert _invitations_cursor(out["next_cursor"])[0] == 100


def test_list_invitations_foreign_cursor_raises_locally():
    """Un curseur qui n'est pas le nôtre est refusé ICI, avec un message qui se
    lit — transmis, il rendait le 400 « Unexpected parameters: type »."""
    rec = []
    c = _client(canned={"data": []}, recorder=rec)
    with pytest.raises(UnipileError, match="invalid cursor"):
        c.list_invitations(direction="sent", cursor="eyJvZmZzZXQiOjEwMH0=")
    assert rec == []                     # rien n'est parti en amont


def test_list_invitations_limit_bounds():
    """101 et 200 rendaient `Unipile 400: Invalid querystring`, qui ne nomme ni
    le param ni la borne (observé 2026-09-10). On tranche localement."""
    rec = []
    c = _client(canned={"data": []}, recorder=rec)
    with pytest.raises(UnipileError, match="between 1 and 100"):
        c.list_invitations(direction="sent", limit=200)
    assert rec == []
    c.list_invitations(direction="sent", limit=100)   # la borne passe
    assert rec[0][2]["limit"] == 100


def test_list_invitations_upstream_cursor_wins():
    """Si Unipile se met un jour à rendre un vrai `next_cursor`, il gagne sur
    le nôtre."""
    c = _client(canned={"data": [{"id": "a"}], "next_cursor": "REAL"})
    out = c.list_invitations(direction="sent", limit=1)
    assert out["next_cursor"] == "REAL" and out["cursor"] == "REAL"


def test_list_invitations_amont_qui_ignore_offset_coupe_au_2e_appel():
    """L'ARRÊT MÉCANIQUE, et le cœur de ce fichier.

    Qu'`offset` pagine réellement `relation-requests` n'a JAMAIS été vérifié
    contre le service réel. Ce banc simule l'amont du cas où l'hypothèse est
    fausse : il ignore `offset` et ressert les mêmes 50 invitations à chaque
    tour. Sans garde, la boucle appelante ne rencontre jamais de fin — mesuré
    avant la garde : 8 pages, 400 lignes rendues pour 50 distinctes.

    Le curseur portant l'empreinte de la page qui l'a produit, le 2e appel
    reconnaît la page servie au 1er et ne fabrique plus de curseur : la boucle
    s'arrête là. Deux appels, pas huit."""
    page = [{"id": f"inv-{i}"} for i in range(50)]
    rec = []
    c = _client(canned=lambda *a: {"data": list(page)}, recorder=rec)

    vus, cursor, appels, out = [], None, 0, {}
    while appels < 8:              # 8 = ce que faisait la boucle sans la garde
        out = c.list_invitations(direction="sent", limit=50, cursor=cursor)
        appels += 1
        vus += [it["id"] for it in out["data"]]
        cursor = out.get("next_cursor")
        if not cursor:
            break

    assert appels == 2, f"la boucle a tourné {appels} fois au lieu de 2"
    assert len(rec) == 2, "un appel de trop est parti en amont"
    assert len(vus) == 100 and len(set(vus)) == 50
    assert "cursor" not in out, "un curseur rendu relance la boucle à vide"
    assert "pagination_note" in out, "l'arrêt doit se DIRE, pas seulement se faire"


def test_list_invitations_amont_qui_pagine_vraiment_va_au_bout():
    """La contre-épreuve de la garde : elle ne doit couper QUE sur une page
    identique. Un amont qui honore `offset` déroule jusqu'à la page vide sans
    être arrêté en route — sinon la garde ferait perdre des invitations."""
    total = 130

    def amont(method, path, params, body):
        off = params.get("offset", 0)
        lim = params.get("limit", 20)
        return {"data": [{"id": f"inv-{i}"}
                         for i in range(off, min(off + lim, total))]}

    c = _client(canned=amont)
    vus, cursor, appels = [], None, 0
    while appels < 20:
        out = c.list_invitations(direction="sent", limit=50, cursor=cursor)
        appels += 1
        vus += [it["id"] for it in out["data"]]
        cursor = out.get("next_cursor")
        if not cursor:
            break

    assert len(set(vus)) == total, "la garde a coupé un amont qui paginait"
    assert appels == 4                      # 50 + 50 + 30, puis la page vide


def test_list_invitations_curseur_sans_empreinte_reste_decode():
    """Un curseur rendu par la version d'avant (`off:<n>`, sans empreinte) ne
    casse pas en vol : il se décode en offset, et faute d'empreinte à comparer
    la page suivante est servie normalement."""
    rec = []
    c = _client(canned={"data": [{"id": "a"}]}, recorder=rec)
    out = c.list_invitations(direction="sent", limit=50, cursor="off:50")
    assert rec[0][2]["offset"] == 50
    assert _invitations_cursor(out["next_cursor"])[0] == 100


def test_list_invitations_refus_ne_prescrit_pas_un_param_hors_face():
    """Le message d'un refus est du texte SERVI : il pilote l'agent qui le lit.
    Il ne doit donc conseiller aucun paramètre que la face refuse — l'outil MCP
    des invitations n'expose que `cursor` et `limit`. Prescrire `offset=`
    envoyait l'agent vers un paramètre inexistant chez lui."""
    c = _client(canned={"data": []})
    with pytest.raises(UnipileError) as e:
        c.list_invitations(direction="sent", cursor="pas-le-notre")
    msg = str(e.value)
    assert "offset" not in msg, (
        "le refus renvoie l'agent vers `offset`, que sa face n'expose pas")
    assert "cursor" in msg, "un refus doit nommer ce qu'il faut repasser"


def test_send_invitation_body():
    rec = []
    c = _client(canned={}, recorder=rec)
    c.send_invitation("ACoAAA", message="hi")
    _, path, _, json = rec[0]
    assert path == "/acc/users/me/relation-requests"
    assert json == {"user_id": "ACoAAA", "message": "hi"}


def test_handle_invitation_accept_cancel():
    rec = []
    c = _client(canned={}, recorder=rec)
    c.handle_invitation("REQ1", "secret", action="accept")
    assert rec[0][1] == "/acc/users/me/relation-requests/REQ1/accept"
    c.handle_invitation("REQ2", "secret", action="decline")
    assert rec[1][1] == "/acc/users/me/relation-requests/REQ2/cancel"


# ---- messagerie ----------------------------------------------------------

def test_send_message_existing_vs_new_chat():
    rec = []
    c = _client(canned={}, recorder=rec)
    c.send_message("hello", chat_id="CH1")
    assert rec[0][1] == "/acc/chats/CH1/messages/send"
    assert rec[0][3] == {"text": "hello"}
    c.send_message("hi", attendee_id="ACoAAA")
    # v2 : nouveau fil LinkedIn via l'inbox (le /chats/send générique → 501, #199/#200).
    assert rec[1][1] == "/acc/inboxes/CLASSIC_PRIMARY/chats/send"
    assert rec[1][3] == {"users_ids": ["ACoAAA"], "text": "hi"}


# ---- forme d'endpoint par provider (inbox vs plate) ----------------------
# Unipile expose la MÊME opération sous deux routes — « GET /v2/:account_id/chats or
# GET /v2/:account_id/inboxes/:inbox_id/chats **if the provider uses inboxes** » — et
# répond 501 à la mauvaise, dans les deux sens. Ce qu'on verrouille ici, c'est la
# DÉRIVATION (provider → route) et le rattrapage sur le signal 501, pas une route
# qu'on croit juste : la bascule en dur vers l'inbox (correcte pour LinkedIn) avait
# cassé les cinq canaux sans inbox, dont WhatsApp.

def _erroring_client(status_by_path, provider=None, recorder=None):
    """Client stubé qui lève `UnipileError(status_code=…)` sur les paths déclarés
    dans `status_by_path`, et rend `{"data": []}` sur les autres."""
    c = UnipileClient(api_key="k", account_id="acc", provider=provider)

    def fake(method, path, params=None, json=None, timeout=None):
        if recorder is not None:
            recorder.append((method, path, params, json))
        status = status_by_path.get(path)
        if status is not None:
            raise UnipileError(f"Unipile {status}: nope", status_code=status)
        return {"data": []}

    c._request = fake  # type: ignore[method-assign]
    return c


def test_uses_inboxes_derives_from_provider():
    mk = lambda p: UnipileClient(api_key="k", account_id="acc", provider=p)  # noqa: E731
    assert mk("LINKEDIN").uses_inboxes()
    assert mk("linkedin").uses_inboxes()          # casse indifférente
    assert mk(None).uses_inboxes()                # non déclaré → LinkedIn (compat)
    for chan in ("WHATSAPP", "TELEGRAM", "INSTAGRAM", "MESSENGER", "TWITTER"):
        assert not mk(chan).uses_inboxes(), chan


def test_list_chats_plain_shape_for_a_provider_without_inbox():
    rec = []
    c = _client(canned={"data": []}, recorder=rec)
    c.provider = "WHATSAPP"
    c.list_chats(limit=5)
    # une seule requête, sur la route plate : pas d'aller-retour 501 à payer.
    assert [r[1] for r in rec] == ["/acc/chats"]
    assert rec[0][2] == {"limit": 5}


def test_list_chats_heals_when_upstream_501s_on_the_declared_shape():
    # provider non déclaré → inbox tentée d'abord ; Unipile dit « pas cette forme ».
    rec = []
    c = _erroring_client({"/acc/inboxes/CLASSIC_PRIMARY/chats": 501}, recorder=rec)
    out = c.list_chats()
    assert [r[1] for r in rec] == ["/acc/inboxes/CLASSIC_PRIMARY/chats", "/acc/chats"]
    assert out["items"] == []
    # …et le symétrique : provider sans inbox qu'Unipile reclasserait côté inbox.
    rec.clear()
    c2 = _erroring_client({"/acc/chats": 501}, provider="WHATSAPP", recorder=rec)
    c2.list_chats()
    assert [r[1] for r in rec] == ["/acc/chats", "/acc/inboxes/CLASSIC_PRIMARY/chats"]


def test_only_501_switches_shape():
    """Un 404/401/500 est une vraie erreur : elle remonte, sans seconde route."""
    for status in (400, 401, 404, 500):
        rec = []
        c = _erroring_client({"/acc/inboxes/CLASSIC_PRIMARY/chats": status},
                             recorder=rec)
        with pytest.raises(UnipileError) as ei:
            c.list_chats()
        assert ei.value.status_code == status
        assert [r[1] for r in rec] == ["/acc/inboxes/CLASSIC_PRIMARY/chats"]


def test_send_new_chat_plain_shape_for_a_provider_without_inbox():
    rec = []
    c = _client(canned={}, recorder=rec)
    c.provider = "WHATSAPP"
    c.send_message("hi", attendee_id="33600000000@s.whatsapp.net")
    assert [r[1] for r in rec] == ["/acc/chats/send"]
    # même corps que la forme inbox : seule la route change.
    assert rec[0][3] == {"users_ids": ["33600000000@s.whatsapp.net"], "text": "hi"}


def test_send_new_chat_heals_on_501():
    rec = []
    c = _erroring_client({"/acc/chats/send": 501}, provider="WHATSAPP", recorder=rec)
    c.send_message("hi", attendee_id="X")
    assert [r[1] for r in rec] == ["/acc/chats/send",
                                   "/acc/inboxes/CLASSIC_PRIMARY/chats/send"]


def test_reply_in_existing_chat_is_shape_agnostic():
    """Répondre dans un fil existant n'a qu'une route (pas d'inbox dans le path) :
    le canal n'y change rien — c'est bien la LISTE qui était cassée pour WhatsApp."""
    for prov in (None, "LINKEDIN", "WHATSAPP"):
        rec = []
        c = _client(canned={}, recorder=rec)
        c.provider = prov
        c.send_message("hello", chat_id="CH1")
        c.list_messages("CH1")
        assert [r[1] for r in rec] == ["/acc/chats/CH1/messages/send",
                                       "/acc/chats/CH1/messages"]


def test_factory_passes_provider():
    assert make_unipile_client(api_key="k", provider="whatsapp").provider == "WHATSAPP"
    assert make_unipile_client(api_key="k").provider is None


def test_react_message_requires_chat_id_in_v2():
    c = _client(canned={})
    with pytest.raises(UnipileError):
        c.react_message("MSG1", "👍")   # pas de chat_id → erreur claire


def test_react_message_path_with_chat_id():
    rec = []
    c = _client(canned={}, recorder=rec)
    c.react_message("MSG1", "👍", chat_id="CH1")
    assert rec[0][1] == "/acc/chats/CH1/messages/MSG1/reactions"


# ---- inmail (#178) + contrats -------------------------------------------

def test_inmail_balance_uses_v2_route():
    rec = []
    c = _client(canned={"object": "InmailCredits", "credits": 5}, recorder=rec)
    out = c.inmail_balance()
    assert rec[0][1] == "/acc/linkedin/inmail-credits"   # ≠ v1 inmail/balance
    assert out["credits"] == 5


# ---- recherche -----------------------------------------------------------

def test_search_people_body_and_path():
    rec = []
    c = _client(canned={"data": []}, recorder=rec)
    # ids numériques → pas de résolution de facette (pas d'appel réseau)
    c.search(keywords="CTO", company=["123"], location=["456"],
             network_distance=[1])
    method, path, _, json = rec[0]
    assert method == "POST" and path == "/acc/linkedin/search/people"
    assert json["keywords"] == "CTO"
    assert json["current_company"] == ["123"]   # v2 : current_company
    assert json["location"] == ["456"]
    assert json["network_distance"] == [1]


def test_search_companies_path():
    rec = []
    c = _client(canned={"data": []}, recorder=rec)
    c.search(category="companies", keywords="ESN", location=["456"])
    assert rec[0][1] == "/acc/linkedin/search/companies"


def test_search_url_timeout_becomes_clean_error():
    # #238 (suite) : le mode URL Recruiter peut pendre (searchContextId expiré) → le
    # timeout réseau (UnipileError SANS status HTTP) devient une erreur ACTIONNABLE,
    # pas un timeout MCP opaque de 180s.
    from oto.tools.unipile.client import UnipileClient, UnipileError
    c = UnipileClient(api_key="k", account_id="acc")

    def _raise(method, path, params=None, json=None, timeout=None):
        raise UnipileError("Unipile: erreur réseau (ReadTimeout).")  # pas de status_code
    c._request = _raise  # type: ignore[method-assign]
    with pytest.raises(UnipileError) as ei:
        c.search(url="https://www.linkedin.com/talent/search?x", api="recruiter")
    assert "search context" in str(ei.value)


def test_search_url_http_error_not_masked():
    # Une VRAIE erreur HTTP (status présent) n'est PAS transformée en « contexte expiré ».
    from oto.tools.unipile.client import UnipileClient, UnipileError
    c = UnipileClient(api_key="k", account_id="acc")

    def _raise(method, path, params=None, json=None, timeout=None):
        raise UnipileError("Unipile 400: bad request", status_code=400)
    c._request = _raise  # type: ignore[method-assign]
    with pytest.raises(UnipileError) as ei:
        c.search(url="https://x", api="recruiter")
    assert ei.value.status_code == 400


def test_search_cursor_only_no_body_rebuild():
    # #238 : sur une PAGE (cursor fourni), on n'envoie QUE le cursor — pas de body ni
    # de re-résolution de facettes. `company`/`location` sont des NOMS (résolus par
    # des GET amont normalement) : la présence d'un SEUL appel prouve le court-circuit
    # (le re-build empilait ces GET → timeout 180s sur Recruiter).
    rec = []
    c = _client(canned={"data": []}, recorder=rec)
    c.search(keywords="CTO", company=["Acme"], location=["Paris"],
             api="recruiter", cursor="CUR123")
    assert len(rec) == 1                              # un seul appel amont
    method, path, params, body = rec[0]
    assert method == "POST" and path.endswith("/people")
    assert params == {"cursor": "CUR123"}
    assert body == {}                                # body VIDE (aucune facette re-résolue)


# ---- erreurs propres (#177/#178) ----------------------------------------

def test_network_error_mapped_and_account_sanitized():
    c = UnipileClient(api_key="k", account_id="acc-SECRET")

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("net::ERR_NAME_NOT_RESOLVED")

    c.session.request = boom  # type: ignore[method-assign]
    with pytest.raises(UnipileError) as e:
        c.list_relations()
    assert "network error" in str(e.value)
    assert "ERR_NAME_NOT_RESOLVED" not in str(e.value)   # pas de fuite scraper


def test_http_error_sanitizes_account_id():
    c = UnipileClient(api_key="k", account_id="acc-SECRET")

    class Resp:
        status_code = 404
        reason = "Not Found"
        text = "Cannot GET /v2/acc-SECRET/linkedin/inmail-credits"

        def json(self):
            return {"message": "Cannot GET /v2/acc-SECRET/linkedin/inmail-credits"}

    c.session.request = lambda *a, **k: Resp()  # type: ignore[method-assign]
    with pytest.raises(UnipileError) as e:
        c.inmail_balance()
    assert "acc-SECRET" not in str(e.value)      # account_id caviardé (#178)
    assert "<account>" in str(e.value)


# ---- hosted-auth v2 : body réécrit + réponse sur `link` (deltas live 2026-07-06) --

def test_hosted_auth_link_v2_body_and_link_key():
    rec = []
    c = _client(canned={"object": "HostedAuthLink", "link": "https://auth.unipile.com/x"},
                recorder=rec)
    url = c.hosted_auth_link(providers=["LINKEDIN"], name="nonce1",
                             notify_url="https://x/webhook",
                             success_redirect_url="https://x/ok",
                             failure_redirect_url="https://x/ko")
    assert url == "https://auth.unipile.com/x"        # lu sur `link`, pas `url`
    method, path, _, body = rec[0]
    assert method == "POST" and path == "/auth/link"
    assert body["providers"] == ["linkedin"]          # minuscule (v1 passait LINKEDIN)
    assert "expires_on" in body and "expiresOn" not in body
    assert body["redirect_uri"] == "https://x/ok"     # un seul redirect
    assert "success_redirect_url" not in body and "failure_redirect_url" not in body
    # v2 rejects v1 leftovers (live 400 since 2026-11-11): none of them is sent
    assert not {"name", "notify_url", "type", "api_url", "reconnect_account"} & set(body)


def test_hosted_auth_link_providers_wildcard_when_none():
    rec = []
    c = _client(canned={"link": "u"}, recorder=rec)
    c.hosted_auth_link()
    assert rec[0][3]["providers"] == "*"              # aucun provider → tous


# ---- posts/comments/reactions : slug public → provider_id URN (delta v2) --------

def _member_stub(recorder):
    def fake(method, path, params=None, json=None, timeout=None):
        recorder.append((method, path, params, json))
        if path.endswith("/users/john-doe"):         # get_profile(slug)
            return {"object": "UserProfile", "public_identifier": "john-doe",
                    "id": "ACoAAB123"}
        return {"data": []}
    return fake


def test_member_posts_resolves_slug_to_urn():
    rec = []
    c = UnipileClient(api_key="k", account_id="acc")
    c._request = _member_stub(rec)  # type: ignore[method-assign]
    c.list_member_posts("john-doe")
    paths = [p for _, p, _, _ in rec]
    assert "/acc/users/john-doe" in paths             # a résolu le profil
    assert "/acc/users/ACoAAB123/posts" in paths      # puis posts sur l'URN


def test_member_reactions_urn_skips_profile_lookup():
    rec = []
    c = UnipileClient(api_key="k", account_id="acc")
    c._request = _member_stub(rec)  # type: ignore[method-assign]
    c.list_member_reactions("ACoAAB999")              # déjà un URN
    paths = [p for _, p, _, _ in rec]
    assert paths == ["/acc/users/ACoAAB999/reactions"]  # aucune résolution de profil


# ---- enrichissement attendee des fils (best-effort) ---------------------
# v2 : chats sous `/acc/inboxes/{inbox}/chats`, carnet de contacts sous `/acc/contacts`.

_CHATS = {
    "items": [
        {"id": "c1", "name": None, "attendee_provider_id": "ACo111"},
        {"id": "c2", "name": "Groupe X", "attendee_provider_id": None},
        {"id": "c3", "name": None, "attendee_provider_id": "ACo333"},
    ]
}

_CONTACTS_PAGE = {
    "items": [
        {"provider_id": "ACo111", "name": "Jane Doe",
         "profile_url": "https://linkedin.com/in/jane",
         "specifics": {"occupation": "CEO at Acme"}},
        {"provider_id": "ACo333", "name": "John Smith", "specifics": {}},
    ],
    "cursor": None,
}


def test_chats_enriched_with_attendee_names():
    c = _seq_client([
        ("GET", "/acc/inboxes/CLASSIC_PRIMARY/chats", _CHATS),
        ("GET", "/acc/contacts", _CONTACTS_PAGE),
    ])
    out = c.list_chats(limit=20, with_attendee_names=True)
    by_id = {it["id"]: it for it in out["items"]}
    assert by_id["c1"]["attendee_name"] == "Jane Doe"
    assert by_id["c1"]["attendee_headline"] == "CEO at Acme"
    assert by_id["c1"]["attendee_profile_url"] == "https://linkedin.com/in/jane"
    assert by_id["c3"]["attendee_name"] == "John Smith"
    # fil de groupe sans attendee_provider_id : intact
    assert "attendee_name" not in by_id["c2"]
    # le `name` brut n'est PAS réécrit (exposer le brut)
    assert by_id["c1"]["name"] is None


def test_chats_default_is_raw_no_extra_call():
    c = _seq_client([("GET", "/acc/inboxes/CLASSIC_PRIMARY/chats", {"items": [
        {"id": "c1", "name": None, "attendee_provider_id": "ACo111"}]})])
    out = c.list_chats(limit=20)
    assert "attendee_name" not in out["items"][0]
    assert len(c._calls) == 1  # pas d'appel /contacts


def test_chats_enrichment_failure_still_returns_list():
    c = _seq_client([("GET", "/acc/inboxes/CLASSIC_PRIMARY/chats", {"items": [
        {"id": "c1", "name": None, "attendee_provider_id": "ACo111"}]})])
    # tout appel /contacts lèvera (aucune réponse stubée) → best-effort
    out = c.list_chats(limit=20, with_attendee_names=True)
    assert out["items"][0]["id"] == "c1"
    assert "attendee_name" not in out["items"][0]


def test_enrichment_failure_is_SAID_not_only_logged():
    """Signal #682 : l'agent a conclu « l'enrichissement annoncé dans la doc n'apparaît
    pas », sans pouvoir distinguer une panne d'un carnet incomplet. L'avertissement
    existait — dans le journal serveur, que l'appelant ne lit jamais.

    Best-effort ne veut pas dire muet : ce que l'appelant a DEMANDÉ et n'a pas obtenu
    doit sortir par la réponse."""
    c = _seq_client([("GET", "/acc/inboxes/CLASSIC_PRIMARY/chats", {"items": [
        {"id": "c1", "name": None, "attendee_provider_id": "ACo111"}]})])
    out = c.list_chats(limit=20, with_attendee_names=True)
    an = out["attendee_names"]
    assert an["status"] == "unavailable" and an["asked"] == 1 and an["resolved"] == 0
    # Et il dit quoi FAIRE : l'absence de nom n'est pas l'absence d'interlocuteur.
    assert "last_message.sender" in an["reason"]


def test_resolution_PARTIELLE_est_dite_avec_les_ids_manquants():
    """Le pire des silences : une liste hétérogène. Sans aveu, un fil sans
    `attendee_name` se lit « pas d'interlocuteur » au lieu de « je n'ai pas su le
    nommer » — et c'est indétectable, puisque les fils voisins, eux, ont leur nom."""
    chats = {"items": [
        {"id": "c1", "name": None, "attendee_provider_id": "ACo111"},
        {"id": "c9", "name": None, "attendee_provider_id": "ACoINCONNU"}]}
    c = _seq_client([
        ("GET", "/acc/inboxes/CLASSIC_PRIMARY/chats", chats),
        ("GET", "/acc/contacts", {"items": [
            {"provider_id": "ACo111", "name": "Jane Doe", "specifics": {}}],
            "cursor": None}),
    ])
    out = c.list_chats(limit=20, with_attendee_names=True)
    an = out["attendee_names"]
    assert an["status"] == "partial" and an["asked"] == 2 and an["resolved"] == 1
    assert an["missing_ids"] == ["ACoINCONNU"]
    by_id = {it["id"]: it for it in out["items"]}
    assert by_id["c1"]["attendee_name"] == "Jane Doe"
    assert "attendee_name" not in by_id["c9"]


def test_tout_resolu_ne_dit_RIEN(monkeypatch):
    """Pas d'écart, pas de bruit — et ici ça compte double : une page de fils pèse déjà
    ~105 Ko et sature la fenêtre de l'appelant (même signal #682)."""
    c = _seq_client([
        ("GET", "/acc/inboxes/CLASSIC_PRIMARY/chats", _CHATS),
        ("GET", "/acc/contacts", _CONTACTS_PAGE),
    ])
    out = c.list_chats(limit=20, with_attendee_names=True)
    assert "attendee_names" not in out


def test_un_fil_whatsapp_se_nomme_par_son_objet_user():
    """Signal 1358 : les canaux plats (WhatsApp) ne portent pas d'`attendee_provider_id`
    mais nomment l'interlocuteur dans `user`. L'enrichissement répondait « unavailable »
    pour tous les fils, alors que chacun portait son nom — sans appel au carnet."""
    chats = {"items": [
        {"id": "c1", "user": {"display_name": "Camille",
                              "specifics": {"contact_name": "Camille (atelier)"}}},
        {"id": "c2", "user": {"display_name": "Dominique"}},
        {"id": "g1", "name": "Groupe"},
    ]}
    c = _seq_client([("GET", "/acc/chats", chats)])
    c.provider = "WHATSAPP"
    out = c.list_chats(limit=20, with_attendee_names=True)
    by_id = {it["id"]: it for it in out["items"]}
    assert by_id["c1"]["attendee_name"] == "Camille (atelier)", "le nom du carnet prime"
    assert by_id["c2"]["attendee_name"] == "Dominique"
    assert "attendee_name" not in by_id["g1"], "un groupe n'a pas d'interlocuteur"
    assert "attendee_names" not in out, "tout est nommé : rien à signaler"
    assert all("/contacts" not in call["path"] for call in c._calls)


def test_resolve_attendee_names_stops_early_when_all_resolved():
    page1 = {"items": [{"provider_id": "A", "name": "Ann"}], "cursor": "NEXT"}
    c = _seq_client([("GET", "/acc/contacts", page1)])
    out = c.resolve_attendee_names({"A"})
    assert out["A"]["name"] == "Ann"
    assert len(c._calls) == 1  # cursor NEXT jamais suivi : tout est résolu


def test_resolve_attendee_names_paginates_until_found():
    page1 = {"items": [{"provider_id": "X", "name": "Xen"}], "cursor": "NEXT"}
    page2 = {"items": [{"provider_id": "B", "name": "Bob"}], "cursor": None}
    c = _seq_client([("GET", "/acc/contacts", page1), ("GET", "/acc/contacts", page2)])
    out = c.resolve_attendee_names({"B"})
    assert out["B"]["name"] == "Bob"
    assert len(c._calls) == 2
    assert c._calls[1]["params"]["cursor"] == "NEXT"


class _Redirige:
    """Une 3xx vers un autre hôte, dont le `Location` garde la clé en query."""
    status_code = 302
    headers = {"Location": "https://ailleurs.test/v1/x?api_key=SECRET#f"}
    content = b""
    text = ""
    ok = False

    def json(self):
        return {}

    def raise_for_status(self):
        return None


def test_une_redirection_est_refusee_jamais_suivie(monkeypatch):
    """La clé part dans un en-tête maison que `requests` ne retire pas sur un changement
    d'hôte : une 3xx est refusée (`RedirectRefused`), jamais suivie ni retentée, et
    sa cible est réduite à schéma, hôte et chemin — jamais sa query."""
    from oto.tools.http.client import RedirectRefused
    vus = []

    def fake(method, url, **kw):
        vus.append(kw)
        return _Redirige()

    c = UnipileClient(api_key="k", account_id="acc")
    c.session.request = fake
    with pytest.raises(RedirectRefused) as e:
        c._request("GET", "/x")
    assert vus[0]["allow_redirects"] is False
    assert e.value.location == "https://ailleurs.test/v1/x"
    assert "SECRET" not in str(e.value)
    assert len(vus) == 1

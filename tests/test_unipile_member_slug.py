"""Le slug d'un profil, sous toutes ses formes, donne la même requête.

Un slug à caractère non ASCII se donne souvent percent-encodé
(`marie-dupont-n%c3%a9e-martin-123456789`) ; l'API répond avec le
`public_identifier` décodé (`marie-dupont-née-martin-123456789`). Le slug
encodé, le slug décodé (NFC ou NFD) et l'adresse du profil, sous l'une ou
l'autre forme, partent sur la même URL — celle que le slug encodé envoyait
déjà et à laquelle l'API rendait le bon profil — et la garde d'identité
(#153) les accepte tous face au `public_identifier` décodé.
"""
import pytest

from oto.tools.unipile import UnipileClient
from oto.tools.unipile.client import UnipileError

DECODED = "marie-dupont-n\u00e9e-martin-123456789"
PROFILE = {"object": "UserProfile", "public_identifier": DECODED,
           "id": "ACoAAD1234", "provider_id": "ACoAAD1234"}
SENT = "https://api.unipile.com/v2/acc/users/marie-dupont-n%25c3%25a9e-martin-123456789"

FORMS = [
    "marie-dupont-n%c3%a9e-martin-123456789",                          # encodé
    "marie-dupont-n%C3%A9e-martin-123456789",                          # encodé, hexa majuscule
    DECODED,                                                           # décodé, NFC
    "marie-dupont-ne\u0301e-martin-123456789",                    # décodé, NFD
    " marie-dupont-n%c3%a9e-martin-123456789/ ",                       # espaces et slash final
    "https://www.linkedin.com/in/marie-dupont-n%c3%a9e-martin-123456789/",
    f"https://www.linkedin.com/in/{DECODED}/",
    f"https://fr.linkedin.com/in/{DECODED}?utm_source=share#about",
    "linkedin.com/in/marie-dupont-n%c3%a9e-martin-123456789/en",
    "HTTPS://WWW.LINKEDIN.COM/in/marie-dupont-n%c3%a9e-martin-123456789",
]


class _Answer:
    status_code = 200
    headers: dict = {}

    def __init__(self, body):
        self._body = body
        self.text = "x"

    def json(self):
        return self._body


def _client(answer):
    c = UnipileClient(api_key="k", account_id="acc")
    sent = []

    def request(method, url, **kw):
        sent.append(url)
        return _Answer(answer)

    c.session.request = request  # type: ignore[method-assign]
    return c, sent


@pytest.mark.parametrize("identifier", FORMS)
def test_each_form_of_the_slug_sends_the_same_request_and_passes_the_guard(identifier):
    c, sent = _client(PROFILE)
    out = c.get_profile(identifier)
    assert sent == [SENT]
    assert out["public_identifier"] == DECODED


def test_the_guard_accepts_an_encoded_answer_too():
    c, _ = _client(dict(PROFILE, public_identifier="marie-dupont-n%C3%A9e-martin-123456789"))
    assert c.get_profile(DECODED)["id"] == "ACoAAD1234"


def test_another_member_is_still_rejected():
    c, _ = _client(dict(PROFILE, public_identifier="marie-dupont-martin-1", id="ACoAAD9999",
                        provider_id="ACoAAD9999"))
    with pytest.raises(UnipileError) as e:
        c.get_profile("marie-dupont-n%c3%a9e-martin-123456789")
    assert "identity_mismatch" in str(e.value)


@pytest.mark.parametrize("identifier, path", [
    ("john-doe", "/users/john-doe"),
    ("John-Doe", "/users/John-Doe"),
    ("https://www.linkedin.com/in/john-doe/", "/users/john-doe"),
    ("ACoAAB1x2y3", "/users/ACoAAB1x2y3"),
    ("urn:li:fsd_profile:ACoAAB1x2y3", "/users/urn%3Ali%3Afsd_profile%3AACoAAB1x2y3"),
])
def test_ascii_slugs_and_provider_ids_are_escaped_once_as_before(identifier, path):
    c, sent = _client({"object": "UserProfile", "public_identifier": "john-doe",
                       "id": "ACoAAB1x2y3", "member_urn": "urn:li:fsd_profile:ACoAAB1x2y3"})
    c.get_profile(identifier)
    assert sent == [f"https://api.unipile.com/v2/acc{path}"]


def test_member_activity_resolves_an_encoded_slug_to_its_provider_id():
    """`_member_id` passe par `get_profile` : un slug encodé n'y bute plus sur la garde."""
    c, sent = _client(PROFILE)
    assert c._member_id("marie-dupont-n%c3%a9e-martin-123456789") == "ACoAAD1234"
    assert sent == [SENT]


def test_the_company_guard_compares_the_decoded_forms_too():
    c, sent = _client({"object": "CompanyProfile", "public_identifier": "société-générale-x",
                       "id": "1234"})
    assert c.get_company("soci%c3%a9t%c3%a9-g%c3%a9n%c3%a9rale-x")["id"] == "1234"
    assert len(sent) == 1


@pytest.mark.parametrize("requested, answered", [
    ("marie-dupont-nee-martin-123456789", DECODED),   # lu sous la forme sans accents
    (DECODED, "marie-dupont-nee-martin-123456789"),   # et l'inverse
])
def test_the_guard_accepts_the_same_slug_with_or_without_its_accents(requested, answered):
    c, _ = _client(dict(PROFILE, public_identifier=answered))
    assert c.get_profile(requested)["id"] == "ACoAAD1234"

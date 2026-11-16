"""La surface de `JefactureClient` et de son module d'auth est un CONTRAT, pas un détail.

oto-backend épingle oto-core par tag et importe `oto.tools.jefacture` : renommer une
méthode, changer un défaut ou déplacer un symbole hors de `client.py` ou `auth.py`
casse un consommateur au bump du pin, loin d'ici. Ce test fige les membres AVEC
leurs signatures, les noms importables, la correspondance entre les fonctions de la
description et les méthodes du client, et la taille des modules du paquet.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la
constante EN CONNAISSANCE DE CAUSE, jamais par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.jefacture as pkg
import oto.tools.jefacture.auth as auth
import oto.tools.jefacture.client as mod

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "jefacture" / "connector.yaml"

EXPECTED_MEMBERS = {
    '_bounded': "(name: 'str', value: 'Optional[int]', low: 'int', high: 'Optional[int]' = None) -> 'Optional[int]'",
    '_choice': "(name: 'str', value: 'Optional[str]', allowed: 'tuple') -> 'Optional[str]'",
    '_day': "(name: 'str', value: 'Optional[str]') -> 'Optional[str]'",
    '_get': "(self, path: 'str', params: 'Optional[Dict[str, Any]]' = None) -> 'Any'",
    '_instant': "(name: 'str', value: 'Optional[str]') -> 'Optional[str]'",
    '_space': "(self, space_id: 'Any') -> 'str'",
    '_text': "(name: 'str', value: 'Optional[str]') -> 'Optional[str]'",
    '_uuid': "(name: 'str', value: 'Any') -> 'str'",
    'get_connection': "(self, space_id: 'str', connection_id: 'str') -> 'Any'",
    'get_document': "(self, space_id: 'str', document_id: 'str') -> 'Any'",
    'get_me': "(self) -> 'Any'",
    'get_payment': "(self, space_id: 'str', payment_id: 'str') -> 'Any'",
    'list_account_movements': "(self, space_id: 'str', payment_account_id: 'str', *, booking_type: 'Optional[str]' = None, date_from: 'Optional[str]' = None, date_to: 'Optional[str]' = None, page: 'Optional[int]' = None, page_size: 'Optional[int]' = None) -> 'Any'",
    'list_balance_offsets': "(self, space_id: 'str', document_id: 'str') -> 'Any'",
    'list_connections': "(self, space_id: 'str', *, relation_type: 'Optional[str]' = None, search: 'Optional[str]' = None, identifier: 'Optional[str]' = None, page: 'Optional[int]' = None, page_size: 'Optional[int]' = None) -> 'Any'",
    'list_documents': "(self, space_id: 'str', *, folder: 'Optional[str]' = None, category: 'Optional[str]' = None, status_query: 'Optional[str]' = None, issued_from: 'Optional[str]' = None, issued_to: 'Optional[str]' = None, due_from: 'Optional[str]' = None, due_to: 'Optional[str]' = None, updated_since: 'Optional[str]' = None, document_number: 'Optional[str]' = None, counterpart: 'Optional[str]' = None, sort_by: 'Optional[str]' = None, page: 'Optional[int]' = None, page_size: 'Optional[int]' = None) -> 'Any'",
    'list_payment_accounts': "(self, space_id: 'str') -> 'Any'",
    'list_reconciliations': "(self, space_id: 'str', *, matching_status: 'Optional[str]' = None, page: 'Optional[int]' = None, page_size: 'Optional[int]' = None) -> 'Any'",
    'list_spaces': "(self, page: 'Optional[int]' = None, page_size: 'Optional[int]' = None) -> 'Any'",
}

EXPECTED_AUTH = {
    'authorize_url': "(client_id: 'str', redirect_uri: 'str', state: 'str') -> 'str'",
    'client_credentials_token': "(client_id: 'str', client_secret: 'str') -> 'Grant'",
    'exchange_code': "(client_id: 'str', client_secret: 'str', code: 'str', redirect_uri: 'str') -> 'Grant'",
    'refresh': "(client_id: 'str', client_secret: 'str', refresh_token: 'str') -> 'Grant'",
}

#: Méthode publique que la description ne porte pas, et pourquoi : aucune.
OUTSIDE_DESCRIPTION: set = set()


def _members():
    return {n: str(inspect.signature(getattr(mod.JefactureClient, n)))
            for n in dir(mod.JefactureClient)
            if not n.startswith("__") and callable(getattr(mod.JefactureClient, n))}


def test_membres_et_signatures_figes():
    assert _members() == EXPECTED_MEMBERS


def test_auth_figee():
    assert {n: str(inspect.signature(getattr(auth, n))) for n in EXPECTED_AUTH} == EXPECTED_AUTH
    for name in ("Grant", "JefactureAuthError", "JefactureGrantExpired", "AUTHORIZE_URL",
                 "TOKEN_URL", "REFRESH_URL", "CLIENT_CREDENTIALS_URL", "SCOPES",
                 "DEFAULT_EXPIRES_IN", "USER_AGENT"):
        assert hasattr(auth, name), name
    assert issubclass(auth.JefactureGrantExpired, auth.JefactureAuthError)


def test_noms_importables():
    assert pkg.__all__ == ["JefactureClient"]
    for name in ("JefactureClient", "BASE_URL", "FOLDERS", "CATEGORIES", "DOCUMENT_SORTS",
                 "RELATION_TYPES", "MATCHING_STATUSES", "BOOKING_TYPES",
                 "RETRY_STATUSES", "MAX_ATTEMPTS", "HTTP_TIMEOUT"):
        assert hasattr(mod, name), name


def test_une_fonction_decrite_est_une_methode_du_meme_nom():
    described = {f["name"] for f in yaml.safe_load(
        DESCRIPTION.read_text(encoding="utf-8"))["functions"]}
    public = {n for n in EXPECTED_MEMBERS if not n.startswith("_")}
    assert described == public - OUTSIDE_DESCRIPTION


def test_les_arguments_decrits_sont_ceux_de_la_methode():
    for fn in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]:
        params = set(inspect.signature(getattr(mod.JefactureClient, fn["name"])).parameters)
        assert set(fn["input"]["properties"]) == params - {"self"}, fn["name"]


def test_les_enumerations_decrites_sont_celles_du_client():
    fns = {f["name"]: f for f in yaml.safe_load(
        DESCRIPTION.read_text(encoding="utf-8"))["functions"]}
    props = fns["list_documents"]["input"]["properties"]
    assert tuple(props["folder"]["enum"]) == mod.FOLDERS
    assert tuple(props["category"]["enum"]) == mod.CATEGORIES
    assert tuple(props["sort_by"]["enum"]) == mod.DOCUMENT_SORTS
    assert tuple(fns["list_connections"]["input"]["properties"]["relation_type"]["enum"]) \
        == mod.RELATION_TYPES
    assert tuple(fns["list_reconciliations"]["input"]["properties"]["matching_status"]["enum"]) \
        == mod.MATCHING_STATUSES
    assert tuple(fns["list_account_movements"]["input"]["properties"]["booking_type"]["enum"]) \
        == mod.BOOKING_TYPES


def test_aucun_module_de_500_lignes_ou_plus():
    package = pathlib.Path(mod.__file__).parent
    for path in package.rglob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path

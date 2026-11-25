"""La surface de `SalesforceClient` est un CONTRAT, pas un détail.

Un consommateur épingle la lib par tag et importe `oto.tools.salesforce` : renommer une méthode, changer un défaut ou
déplacer un symbole casse un consommateur au bump du pin, loin d'ici (oto 1 construit le client avec `on_refresh` et lit
`SalesforceAuthError`). Ce test fige les membres AVEC leurs signatures, les noms importables et la correspondance entre
les fonctions de la description et les méthodes du client.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la constante EN CONNAISSANCE DE CAUSE, jamais
par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.salesforce as pkg
import oto.tools.salesforce.client as mod

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "salesforce" / "connector.yaml"

EXPECTED_MEMBERS = {
    '_collection_records': '(self, sobject: str, items: list[dict]) -> list[dict]',
    '_get_access_token': '(self) -> tuple[str, str]',
    '_invalidate_token': '(self)',
    '_request': '(self, method: str, path: str, **kwargs) -> Any',
    '_soql_escape': '(value: str) -> str',
    '_url_for': '(self, instance_url: str, path: str) -> str',
    'create_note': '(self, parent_id: str, title: str, body: str) -> dict',
    'create_record': '(self, sobject: str, data: dict) -> dict',
    'create_records': '(self, sobject: str, items: list[dict], all_or_none: bool = False) -> list[dict]',
    'delete_record': '(self, sobject: str, record_id: str) -> dict',
    'describe': '(self, sobject: str) -> dict',
    'get_record': '(self, sobject: str, record_id: str, fields: Optional[str] = None) -> dict',
    'list_notes': '(self, parent_id: str) -> list[dict]',
    'list_records': '(self, sobject: str, fields: Optional[str] = None, where: Optional[str] = None, limit: int = 200) -> dict',
    'query': '(self, soql: str) -> dict',
    'query_more': '(self, next_records_url: str) -> dict',
    'search': '(self, sosl: str) -> dict',
    'update_record': '(self, sobject: str, record_id: str, data: dict) -> dict',
    'update_records': '(self, sobject: str, items: list[dict], all_or_none: bool = False) -> list[dict]',
    'upsert_record': '(self, sobject: str, external_id_field: str, external_id: str, data: dict) -> dict',
}
EXPECTED_INIT = ("(self, client_id: Optional[str] = None, client_secret: Optional[str] = None, "
                 "refresh_token: Optional[str] = None, login_url: Optional[str] = None, "
                 "on_refresh: Optional[Callable[[dict], NoneType]] = None, *, "
                 "session: Optional[requests.sessions.Session] = None)")

#: Méthodes publiques que la description ne porte pas (motifs : `LEFT_OUT` de `tests/test_salesforce_description.py`).
OUTSIDE_DESCRIPTION = {
    "create_record", "update_record", "upsert_record", "create_records", "update_records",
    "list_records", "list_notes", "create_note",
}


def _members():
    return {n: str(inspect.signature(getattr(mod.SalesforceClient, n)))
            for n in dir(mod.SalesforceClient)
            if not n.startswith("__") and callable(getattr(mod.SalesforceClient, n))}


def _functions():
    return {f["name"]: f for f in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]}


def test_membres_et_signatures_figes():
    assert _members() == EXPECTED_MEMBERS
    assert str(inspect.signature(mod.SalesforceClient.__init__)) == EXPECTED_INIT


def test_noms_importables():
    assert pkg.__all__ == ["SalesforceAuthError", "SalesforceClient", "SalesforceHostRefused"]
    assert mod.SalesforceClient.API_VERSION == "v60.0"
    assert mod.SalesforceClient.MAX_COLLECTION_RECORDS == 200
    assert set(mod.SalesforceClient.DEFAULT_FIELDS) == {"Contact", "Account", "Lead", "Opportunity"}
    assert mod.SalesforceAuthError.status_code == 401
    for name in ("_HTTP_TIMEOUT", "_TOKEN_CACHE", "_cred_key", "_check_salesforce_url", "SALESFORCE_DOMAINS"):
        assert hasattr(mod, name), name


def test_une_fonction_decrite_est_une_methode_du_meme_nom():
    public = {n for n in EXPECTED_MEMBERS if not n.startswith("_")}
    assert set(_functions()) == public - OUTSIDE_DESCRIPTION

"""La surface de `AttioClient` et de ses ressources est un CONTRAT, pas un détail.

Un consommateur épingle la lib par tag et importe `oto.tools.attio` (oto 1 bâtit `AttioResource` pour tout objet) :
renommer une méthode, changer un défaut ou déplacer un symbole casse un consommateur au bump du pin, loin d'ici. Ce test
fige les membres de chaque ressource AVEC leurs signatures, les noms importables et la correspondance entre les
fonctions de la description et les méthodes du client.

⚠️ Ce test échoue si tu ajoutes une méthode : c'est voulu — mets à jour la constante EN CONNAISSANCE DE CAUSE, jamais
par réflexe pour faire passer le vert.
"""
import inspect
import pathlib

import yaml

import oto.tools.attio as pkg
import oto.tools.attio.client as mod

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESCRIPTION = ROOT / "connectors" / "attio" / "connector.yaml"

_CLIENT = "(self, client: 'AttioClient')"
EXPECTED_MEMBERS = {
    'AttioResource': {
        '__init__': "(self, client: 'AttioClient', object_type: str)",
        'create': '(self, **attributes) -> Dict[str, Any]',
        'delete': '(self, record_id: str) -> Dict[str, Any]',
        'get': '(self, record_id: str) -> Dict[str, Any]',
        'list': '(self, limit: int = 50, offset: int = 0, sort: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]',
        'merge': '(self, primary_record_id: str, secondary_record_id: str) -> Dict[str, Any]',
        'search': '(self, query: str = None, filters: Dict[str, Any] = None, limit: int = 50) -> List[Dict[str, Any]]',
        'update': '(self, record_id: str, overwrite_multiselect: bool = False, **attributes) -> Dict[str, Any]',
    },
    'AttioNotes': {
        '__init__': _CLIENT,
        'create': '(self, parent_object: str, parent_record_id: str, title: str, content: str) -> Dict[str, Any]',
        'delete': '(self, note_id: str) -> Dict[str, Any]',
        'get': '(self, note_id: str) -> Dict[str, Any]',
        'list': '(self, parent_object: str = None, parent_record_id: str = None, limit: int = None, offset: int = None) -> List[Dict[str, Any]]',
    },
    'AttioTasks': {
        '__init__': _CLIENT,
        '_get_default_assignee': '(self) -> str',
        'create': '(self, content: str, deadline: str = None, assignee_id: str = None, linked_object: str = None, linked_record_id: str = None) -> Dict[str, Any]',
        'delete': '(self, task_id: str) -> Dict[str, Any]',
        'get': '(self, task_id: str) -> Dict[str, Any]',
        'list': '(self, completed: bool = None, limit: int = None, offset: int = None, sort: str = None) -> List[Dict[str, Any]]',
        'update': '(self, task_id: str, deadline: str = None, is_completed: bool = None, assignee_id: str = None, linked_object: str = None, linked_record_id: str = None) -> Dict[str, Any]',
    },
    'AttioLists': {
        '__init__': _CLIENT,
        'create': "(self, name: str, parent_object: str, api_slug: str = None, workspace_access: str = 'full-access', workspace_member_access: List[Dict[str, Any]] = None) -> Dict[str, Any]",
        'get': '(self, list_id_or_slug: str) -> Dict[str, Any]',
        'list': '(self) -> Dict[str, Any]',
        'update': '(self, list_id_or_slug: str, **attributes) -> Dict[str, Any]',
        'views': '(self, list_id_or_slug: str) -> Dict[str, Any]',
    },
    'AttioEntries': {
        '__init__': _CLIENT,
        'create': '(self, list_id_or_slug: str, parent_record_id: str, parent_object: str, entry_values: Dict[str, Any] = None) -> Dict[str, Any]',
        'delete': '(self, list_id_or_slug: str, entry_id: str) -> Dict[str, Any]',
        'get': '(self, list_id_or_slug: str, entry_id: str) -> Dict[str, Any]',
        'query': '(self, list_id_or_slug: str, filter: Dict[str, Any] = None, sorts: List[Dict[str, Any]] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]',
        'update': '(self, list_id_or_slug: str, entry_id: str, entry_values: Dict[str, Any], overwrite_multiselect: bool = False) -> Dict[str, Any]',
    },
    'AttioWorkspaceMembers': {
        '__init__': _CLIENT,
        'get': '(self, workspace_member_id: str) -> Dict[str, Any]',
        'list': '(self) -> Dict[str, Any]',
    },
    'AttioComments': {
        '__init__': _CLIENT,
        'create': '(self, content: str, author_id: str, thread_id: str = None, parent_object: str = None, parent_record_id: str = None, entry_id: str = None, list_id: str = None) -> Dict[str, Any]',
        'delete': '(self, comment_id: str) -> Dict[str, Any]',
        'get': '(self, comment_id: str) -> Dict[str, Any]',
    },
    'AttioThreads': {
        '__init__': _CLIENT,
        'get': '(self, thread_id: str) -> Dict[str, Any]',
        'list': '(self, parent_object: str = None, parent_record_id: str = None, list_id: str = None, entry_id: str = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]',
    },
    'AttioMeetings': {
        '__init__': _CLIENT,
        'get': '(self, meeting_id: str) -> Dict[str, Any]',
        'list': '(self, limit: int = None, cursor: str = None, sort: str = None, ends_from: str = None, starts_before: str = None) -> Dict[str, Any]',
    },
    'AttioCallRecordings': {
        '__init__': _CLIENT,
        'get': '(self, meeting_id: str, call_recording_id: str) -> Dict[str, Any]',
        'list': '(self, meeting_id: str) -> Dict[str, Any]',
        'transcript': '(self, meeting_id: str, call_recording_id: str, cursor: str = None) -> Dict[str, Any]',
    },
    'AttioObjects': {
        '__init__': _CLIENT,
        'get': '(self, object_id_or_slug: str) -> Dict[str, Any]',
        'list': '(self) -> Dict[str, Any]',
        'views': '(self, object_id_or_slug: str) -> Dict[str, Any]',
    },
    'AttioAttributes': {
        '__init__': _CLIENT,
        'create': '(self, target: str, identifier: str, definition: Dict[str, Any]) -> Dict[str, Any]',
        'create_option': '(self, target: str, identifier: str, attribute: str, title: str) -> Dict[str, Any]',
        'create_status': '(self, target: str, identifier: str, attribute: str, title: str) -> Dict[str, Any]',
        'get': '(self, target: str, identifier: str, attribute: str) -> Dict[str, Any]',
        'list': '(self, target: str, identifier: str) -> Dict[str, Any]',
        'options': '(self, target: str, identifier: str, attribute: str) -> Dict[str, Any]',
        'statuses': '(self, target: str, identifier: str, attribute: str) -> Dict[str, Any]',
    },
    'AttioClient': {
        '__init__': '(self, api_key: str = None)',
        '_request': '(self, method: str, endpoint: str, **kwargs) -> Any',
    },
}
# Les ressources que porte un client, sous leur attribut ; les fiches de tous les objets passent par AttioResource.
EXPECTED_RESOURCES = {
    'companies': 'AttioResource', 'people': 'AttioResource', 'deals': 'AttioResource', 'notes': 'AttioNotes',
    'tasks': 'AttioTasks', 'lists': 'AttioLists', 'entries': 'AttioEntries',
    'workspace_members': 'AttioWorkspaceMembers', 'comments': 'AttioComments', 'threads': 'AttioThreads',
    'meetings': 'AttioMeetings', 'call_recordings': 'AttioCallRecordings', 'objects': 'AttioObjects',
    'attributes': 'AttioAttributes',
}


def _members(cls):
    return {n: str(inspect.signature(getattr(cls, n)))
            for n in dir(cls)
            if (not n.startswith("__") or n == "__init__") and callable(getattr(cls, n))}


def test_membres_et_signatures_figes():
    for name, expected in EXPECTED_MEMBERS.items():
        assert _members(getattr(mod, name)) == expected, name


def test_ressources_figees():
    client = mod.AttioClient(api_key="attio-k")
    resources = {n: type(v).__name__ for n, v in vars(client).items() if n != "api_key"}
    assert resources == EXPECTED_RESOURCES
    assert {client.companies.object_type, client.people.object_type, client.deals.object_type} == {
        "companies", "people", "deals"}


def test_noms_importables():
    assert pkg.__all__ == ["AttioClient"]
    assert pkg.AttioClient is mod.AttioClient
    assert mod.AttioClient.BASE_URL == "https://api.attio.com/v2"
    assert mod.AttioClient.__mro__ == (mod.AttioClient, object)


def test_chaque_fonction_decrite_est_une_methode_publique_dune_ressource():
    functions = [f["name"] for f in yaml.safe_load(DESCRIPTION.read_text(encoding="utf-8"))["functions"]]
    public = sum(1 for members in EXPECTED_MEMBERS.values() for n in members if not n.startswith("_"))
    assert len(functions) == len(set(functions)) == 47
    assert public == 48, "47 functions describe 45 methods (two methods, one function per verb: BY_VERB), 3 left to the client (tests/test_attio_description.py, LEFT_OUT)"

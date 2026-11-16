"""3CX configuration — the PBX's `$metadata` read as a schema, every path checked
against it, credentials masked when read and refused when written, and the
generic read / create / update / delete / invoke of `ThreeCXClient`.

A small `$metadata` in the 3CX form (one namespace, inherited key, enum,
singleton, bound and unbound operations): no network, no real PBX.
"""
import json

import pytest

from oto.tools.threecx import Schema, ThreeCXClient
from oto.tools.threecx import auth as threecx_auth
from oto.tools.threecx import client as threecx_client
from oto.tools.threecx.schema import mask, secret_paths

BASE = "https://pbx.example.test"

METADATA = b"""<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
 <edmx:DataServices>
  <Schema Namespace="Pbx" xmlns="http://docs.oasis-open.org/odata/ns/edm">
   <EnumType Name="RuleConditionType"><Member Name="BasedOnDID" Value="0"/><Member Name="BasedOnCallerID" Value="1"/></EnumType>
   <EntityType Name="Peer"><Key><PropertyRef Name="Id"/></Key>
     <Property Name="Id" Type="Edm.Int32" Nullable="false"/>
     <Property Name="Number" Type="Edm.String"/></EntityType>
   <EntityType Name="User" BaseType="Pbx.Peer">
     <Property Name="FirstName" Type="Edm.String"/>
     <Property Name="AuthPassword" Type="Edm.String"/>
     <Property Name="AllowChangePassword" Type="Edm.Boolean"/>
     <NavigationProperty Name="ForwardingProfiles" Type="Collection(Pbx.ForwardingProfile)"/></EntityType>
   <EntityType Name="ForwardingProfile"><Key><PropertyRef Name="Id"/></Key>
     <Property Name="Id" Type="Edm.Int32"/><Property Name="Name" Type="Edm.String"/></EntityType>
   <EntityType Name="InboundRule"><Key><PropertyRef Name="Id"/></Key>
     <Property Name="Id" Type="Edm.Int32"/><Property Name="RuleName" Type="Edm.String"/>
     <Property Name="Condition" Type="Pbx.RuleConditionType"/>
     <Property Name="OfficeHoursDestination" Type="Pbx.Destination"/></EntityType>
   <EntityType Name="Parameter"><Key><PropertyRef Name="Name"/></Key>
     <Property Name="Name" Type="Edm.String"/><Property Name="Value" Type="Edm.String"/></EntityType>
   <EntityType Name="Pair"><Key><PropertyRef Name="A"/><PropertyRef Name="B"/></Key>
     <Property Name="A" Type="Edm.String"/><Property Name="B" Type="Edm.Int32"/></EntityType>
   <EntityType Name="VoicemailSettings"><Key><PropertyRef Name="Id"/></Key>
     <Property Name="Id" Type="Edm.Int32"/><Property Name="MinDuration" Type="Edm.Int32"/></EntityType>
   <ComplexType Name="Destination"><Property Name="To" Type="Edm.String"/><Property Name="Number" Type="Edm.String"/></ComplexType>
   <Action Name="SendWelcomeEmail" IsBound="true"><Parameter Name="bindingParameter" Type="Pbx.User"/></Action>
   <Function Name="GetPhoneSecret" IsBound="true"><Parameter Name="bindingParameter" Type="Pbx.User"/><ReturnType Type="Edm.String"/></Function>
   <Action Name="BulkUpdate" IsBound="true"><Parameter Name="bindingParameter" Type="Collection(Pbx.User)"/><Parameter Name="ids" Type="Collection(Edm.Int32)"/></Action>
   <Function Name="GetFirstAvailable" IsBound="true"><Parameter Name="bindingParameter" Type="Collection(Pbx.User)"/><Parameter Name="from" Type="Edm.String"/><ReturnType Type="Edm.String"/></Function>
   <Function Name="GetVersion"><ReturnType Type="Edm.String"/></Function>
   <EntityContainer Name="Default">
     <EntitySet Name="Users" EntityType="Pbx.User"/>
     <EntitySet Name="InboundRules" EntityType="Pbx.InboundRule"/>
     <EntitySet Name="Parameters" EntityType="Pbx.Parameter"/>
     <EntitySet Name="Pairs" EntityType="Pbx.Pair"/>
     <Singleton Name="VoicemailSettings" Type="Pbx.VoicemailSettings"/>
   </EntityContainer>
  </Schema>
 </edmx:DataServices>
</edmx:Edmx>"""


@pytest.fixture
def schema():
    return Schema.parse(METADATA)


# --- schema -------------------------------------------------------------------

def test_cle_et_proprietes_heritees(schema):
    user = schema.resource_type("Users")
    assert user.key == (("Id", "Edm.Int32"),)
    assert {"Id", "Number", "FirstName"} <= set(user.properties)
    assert user.navigations == {"ForwardingProfiles": "Collection(Pbx.ForwardingProfile)"}


def test_ressource_inconnue_nommee(schema):
    with pytest.raises(ValueError, match="no resource 'Userz'"):
        schema.resource_type("Userz")


def test_description_enum_operations(schema):
    d = schema.describe("InboundRules")
    assert d["key"] == {"Id": "Edm.Int32"}
    assert d["properties"]["Condition"] == {"type": "Pbx.RuleConditionType",
                                            "values": ["BasedOnDID", "BasedOnCallerID"]}
    assert d["properties"]["OfficeHoursDestination"] == {
        "type": "Pbx.Destination", "properties": {"To": "Edm.String", "Number": "Edm.String"}}
    u = schema.describe("Users")
    assert [o["name"] for o in u["operations_on_entity"]] == ["SendWelcomeEmail"]
    assert {o["name"] for o in u["operations_on_collection"]} == {"BulkUpdate",
                                                                  "GetFirstAvailable"}
    v = schema.describe("VoicemailSettings")
    assert v["kind"] == "singleton" and "key" not in v


@pytest.mark.parametrize("resource, key, attendu", [
    ("Users", 5, "/Users(5)"),
    ("Users", "5", "/Users(5)"),
    ("Parameters", "MAX_CALLS", "/Parameters('MAX_CALLS')"),
    ("Parameters", "l'heure", "/Parameters('l%27%27heure')"),
    ("Parameters", "a/b", "/Parameters('a%2Fb')"),
    ("Pairs", {"A": "x", "B": 3}, "/Pairs(A='x',B=3)"),
])
def test_chemin_cle_typee(schema, resource, key, attendu):
    assert schema.path(resource, key=key) == attendu


@pytest.mark.parametrize("resource, key", [
    ("Users", "5 or 1=1"),
    ("Users", True),
    ("Pairs", "x"),
    ("Pairs", {"A": "x"}),
    ("VoicemailSettings", 1),
])
def test_cle_invalide_refusee(schema, resource, key):
    with pytest.raises(ValueError):
        schema.path(resource, key=key)


def test_navigation_verifiee(schema):
    assert schema.path("Users", key=7, navigation="ForwardingProfiles") == (
        "/Users(7)/ForwardingProfiles")
    with pytest.raises(ValueError, match="no navigation"):
        schema.path("Users", key=7, navigation="Greetings")
    with pytest.raises(ValueError, match="needs the key"):
        schema.path("Users", navigation="ForwardingProfiles")


def test_operations_liees_et_arguments(schema):
    op = schema.operation("GetFirstAvailable", "Users", on_entity=False)
    assert schema.path("Users", operation=op, arguments={"from": "1000"}) == (
        "/Users/Pbx.GetFirstAvailable(from='1000')")
    with pytest.raises(ValueError, match="needs"):
        schema.path("Users", operation=op, arguments={})
    with pytest.raises(ValueError, match="takes no argument"):
        schema.path("Users", operation=op, arguments={"from": "1", "x": 2})
    with pytest.raises(ValueError, match="no operation 'SendWelcomeEmail' on Users\\(\\)"):
        schema.operation("SendWelcomeEmail", "Users", on_entity=False)
    racine = schema.operation("GetVersion", None, on_entity=False)
    assert schema.path(None, operation=racine) == "/GetVersion()"


def test_corps_verifie_contre_le_schema(schema):
    schema.check_body("InboundRules", {"OfficeHoursDestination": {"To": "Queue",
                                                                   "Number": "8003"}})
    with pytest.raises(ValueError, match="no property \\['Destination'\\]"):
        schema.check_body("InboundRules", {"Destination": "8003"})
    with pytest.raises(ValueError, match="non-empty"):
        schema.check_body("InboundRules", {})


def test_secret_jamais_ecrit(schema):
    with pytest.raises(ValueError, match="AuthPassword"):
        schema.check_body("Users", {"FirstName": "x", "AuthPassword": "nouveau"})
    with pytest.raises(ValueError, match="ForwardingProfiles\\[0\\].Token"):
        schema.check_body("Users", {"ForwardingProfiles": [{"Token": "t"}]})


def test_operation_qui_livre_un_secret_refusee(schema):
    with pytest.raises(ValueError, match="never called through oto"):
        schema.operation("GetPhoneSecret", "Users", on_entity=True)
    assert "GetPhoneSecret" not in {o["name"] for o in
                                    schema.describe("Users")["operations_on_entity"]}


def test_masque_les_secrets_seulement():
    row = {"Number": "1001", "AuthPassword": "s3cret", "AllowChangePassword": True,
           "VMPIN": "1234", "AuthID": "abc", "Phones": [{"Secret": "x", "Model": "T46"}],
           "Password": "", "LicenseKey": "FMC2", "ProvLink": "https://x/p/abc",
           "NextPageToken": "n", "Key": "k", "KeyCode": "1"}
    assert mask(row) == {"Number": "1001", "AuthPassword": "***", "AllowChangePassword": True,
                         "VMPIN": "***", "AuthID": "***",
                         "Phones": [{"Secret": "***", "Model": "T46"}], "Password": "",
                         "LicenseKey": "***", "ProvLink": "***", "NextPageToken": "***",
                         "Key": "***", "KeyCode": "1"}
    assert secret_paths(row)[:4] == ["AuthPassword", "VMPIN", "AuthID", "Phones[0].Secret"]


def test_xml_hostile_refuse():
    bombe = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">'
             b'<!ENTITY b "&a;&a;&a;&a;">]><x>&b;</x>')
    with pytest.raises(Exception):
        Schema.parse(bombe)


# --- client -------------------------------------------------------------------

class _Resp:
    def __init__(self, payload=None, status_code=200, content=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content if content is not None else (
            json.dumps(payload).encode() if payload is not None else b"")
        self.headers = headers or {"Content-Type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    @property
    def text(self):
        return self.content.decode()


@pytest.fixture(autouse=True)
def _caches_vides(monkeypatch):
    threecx_auth._TOKEN_CACHE.clear()
    threecx_client._SCHEMA_CACHE.clear()
    monkeypatch.setattr("socket.getaddrinfo",
                        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(threecx_auth.requests, "post", lambda url, **kw: _Resp(
        {"Status": "AuthSuccess", "Token": {"access_token": "t", "expires_in": 3600}}))
    yield
    threecx_client._SCHEMA_CACHE.clear()


@pytest.fixture
def api(monkeypatch):
    """`Session.request` stub: `$metadata` served, other calls recorded and
    answered from `replies`."""
    calls, replies = [], []

    def fake(self, method, url, params=None, json=None, headers=None, **kw):
        if url.endswith("/$metadata"):
            calls.append(("META", url))
            return _Resp(content=METADATA, headers={"Content-Type": "application/xml"})
        calls.append((method, url.split("/xapi/v1", 1)[1], params, json))
        return replies.pop(0) if replies else _Resp({"value": []})

    monkeypatch.setattr("requests.Session.request", fake)
    return calls, replies


def _client():
    return ThreeCXClient(BASE, username="u@example.test", password="pw")


def _sans_meta(calls):
    return [c for c in calls if c[0] != "META"]


def test_schema_en_cache_par_pbx(api):
    calls, _ = api
    _client().read("Users")
    _client().read("InboundRules")
    assert [c for c in calls if c[0] == "META"] == [("META", f"{BASE}/xapi/v1/$metadata")]


def test_lecture_collection_paginee(api):
    calls, replies = api
    replies.append(_Resp({"value": [{"Id": 1}, {"Id": 2}]}))
    out = _client().read("Users", filter="Number eq '1001'", select=["Id", "Number"],
                         orderby="Number", top=2, skip=4)
    assert out == {"value": [{"Id": 1}, {"Id": 2}], "next_skip": 6}
    assert _sans_meta(calls) == [("GET", "/Users", {
        "$filter": "Number eq '1001'", "$select": "Id,Number", "$orderby": "Number",
        "$top": 2, "$skip": 4}, None)]


def test_lecture_derniere_page_et_entite(api):
    calls, replies = api
    replies += [_Resp({"value": [{"Id": 1}]}), _Resp({"Id": 9}), _Resp({"MinDuration": 3})]
    assert _client().read("Users", top=5)["next_skip"] is None
    assert _client().read("Users", key=9) == {"value": {"Id": 9}}
    assert _client().read("VoicemailSettings") == {"value": {"MinDuration": 3}}
    assert [c[2] for c in _sans_meta(calls)][1:] == [None, None]


def test_lecture_navigation_collection(api):
    calls, replies = api
    replies.append(_Resp({"value": [{"Id": 3, "Name": "Away"}]}))
    out = _client().read("Users", key=9, navigation="ForwardingProfiles")
    assert out["value"] == [{"Id": 3, "Name": "Away"}]
    assert _sans_meta(calls)[0][1] == "/Users(9)/ForwardingProfiles"


@pytest.mark.parametrize("kw", [{"top": 0}, {"top": 501}, {"skip": -1},
                                {"select": ["Id,AuthPassword"]}])
def test_lecture_arguments_invalides(api, kw):
    with pytest.raises(ValueError):
        _client().read("Users", **kw)


def test_creation_modification_suppression(api):
    calls, replies = api
    replies += [_Resp({"Id": 12}), _Resp(status_code=204), _Resp(status_code=204),
                _Resp(status_code=204)]
    c = _client()
    assert c.create("InboundRules", {"RuleName": "r"}) == {"Id": 12}
    assert c.update("InboundRules", {"RuleName": "r2"}, key=12) is None
    assert c.update("VoicemailSettings", {"MinDuration": 2}) is None
    assert c.delete("InboundRules", key=12) is None
    assert _sans_meta(calls) == [
        ("POST", "/InboundRules", None, {"RuleName": "r"}),
        ("PATCH", "/InboundRules(12)", None, {"RuleName": "r2"}),
        ("PATCH", "/VoicemailSettings", None, {"MinDuration": 2}),
        ("DELETE", "/InboundRules(12)", None, None),
    ]


def test_ecritures_mal_formees_refusees_sans_requete(api):
    calls, _ = api
    c = _client()
    for fn in (lambda: c.update("InboundRules", {"RuleName": "x"}),
               lambda: c.update("Users", {"AuthPassword": "x"}, key=1),
               lambda: c.create("VoicemailSettings", {"MinDuration": 1}),
               lambda: c.delete("InboundRules", key=None),
               lambda: c.create("Users", {"Nope": 1})):
        with pytest.raises(ValueError):
            fn()
    assert _sans_meta(calls) == []


def test_invocation_action_et_fonction(api):
    calls, replies = api
    replies += [_Resp(status_code=204), _Resp({"value": "1003"}), _Resp({"value": "20.0"}),
                _Resp(status_code=204)]
    c = _client()
    c.invoke("SendWelcomeEmail", resource="Users", key=4)
    assert c.invoke("GetFirstAvailable", resource="Users", arguments={"from": "1000"}) == {
        "value": "1003"}
    c.invoke("GetVersion")
    c.invoke("BulkUpdate", resource="Users", arguments={"ids": [1, 2]})
    assert _sans_meta(calls) == [
        ("POST", "/Users(4)/Pbx.SendWelcomeEmail", None, {}),
        ("GET", "/Users/Pbx.GetFirstAvailable(from='1000')", None, None),
        ("GET", "/GetVersion()", None, None),
        ("POST", "/Users/Pbx.BulkUpdate", None, {"ids": [1, 2]}),
    ]


def test_ressource_au_nom_invalide_refusee(api):
    with pytest.raises(ValueError, match="3CX name"):
        _client().read("Users/../$metadata")

"""PowerBIClient — verrouille le contrat HTTP de l'API REST Power BI, en lecture : les
espaces, les rapports et les modèles d'une personne, une requête DAX, la définition des
mesures (l'expression n'est servie qu'avec le droit d'écriture sur le modèle)."""
import pytest

from oto.tools.microsoft import PowerBIClient, auth as ms_auth, scopes
from microsoft_fake import CID, REDIRECT, _Resp, calls  # noqa: F401

P = "https://api.powerbi.com/v1.0/myorg"


@pytest.fixture
def client():
    return PowerBIClient("AT-powerbi")


def test_espaces_rapports_et_modeles(calls, client):
    calls.responses.extend([_Resp({"value": [{"id": "w1", "name": "Pilotage"}]}),
                            _Resp({"value": [{"id": "r1"}]}),
                            _Resp({"value": [{"id": "r2"}]}),
                            _Resp({"value": [{"id": "d1"}]})])
    assert client.list_workspaces(limit=10) == [{"id": "w1", "name": "Pilotage"}]
    client.list_reports("w1")
    client.list_reports()
    client.list_datasets("w 1")
    assert [(c["method"], c["url"]) for c in calls] == [
        ("GET", f"{P}/groups"), ("GET", f"{P}/groups/w1/reports"), ("GET", f"{P}/reports"),
        ("GET", f"{P}/groups/w%201/datasets")]
    assert calls[0]["params"] == {"$top": 10}
    assert calls[0]["headers"]["Authorization"] == "Bearer AT-powerbi"


def test_une_reponse_sans_liste_est_refusee(calls, client):
    calls.responses.append(_Resp({"oops": 1}))
    with pytest.raises(ValueError, match="value"):
        client.list_datasets()


def test_une_requete_dax(calls, client):
    calls.responses.append(_Resp({"results": [{"tables": [{"rows": [
        {"Ventes[Mois]": "2026-10", "[Total]": 12}, {"Ventes[Mois]": "2026-11", "[Total]": 3}]}]}]}))
    out = client.execute_query("d1", "EVALUATE SUMMARIZE(Ventes, Ventes[Mois])",
                               workspace_id="w1")
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == f"{P}/groups/w1/datasets/d1/executeQueries"
    assert calls[0]["json"] == {"queries": [{"query": "EVALUATE SUMMARIZE(Ventes, Ventes[Mois])"}],
                                "serializerSettings": {"includeNulls": True}}
    assert out == {"columns": ["Mois", "Total"],
                   "rows": [{"Mois": "2026-10", "Total": 12}, {"Mois": "2026-11", "Total": 3}],
                   "truncated": False, "error": None}


def test_deux_colonnes_de_meme_nom_gardent_leur_table(calls, client):
    calls.responses.append(_Resp({"results": [{"tables": [{"rows": [
        {"A[Id]": 1, "B[Id]": 2}]}]}]}))
    assert client.execute_query("d1", "EVALUATE X")["columns"] == ["A[Id]", "B[Id]"]


def test_un_resultat_coupe_le_dit(calls, client):
    calls.responses.append(_Resp({"results": [{
        "tables": [{"rows": [{"[x]": 1}]}],
        "error": {"code": "QueryRowLimit", "message": "row limit reached"}}]}))
    out = client.execute_query("d1", "EVALUATE T")
    assert out["truncated"] is True and out["error"] == "row limit reached"


def test_une_requete_vide_est_refusee_avant_l_appel(calls, client):
    with pytest.raises(ValueError, match="dax is empty"):
        client.execute_query("d1", "  ")
    assert not calls


_MESURE = {"[Name]": "CA", "[Table]": "Ventes", "[Description]": "Chiffre d'affaires",
           "[DisplayFolder]": "Finance", "[FormatString]": "#,0", "[DataType]": "Decimal",
           "[IsHidden]": False, "[State]": "Ready", "[DetailRowsDefinition]": None}


@pytest.mark.parametrize("expression, visible", [("SUM(Ventes[Montant])", True), ("", False)])
def test_les_mesures_et_leur_formule(calls, client, expression, visible):
    calls.responses.append(_Resp({"results": [{"tables": [{"rows": [
        {**_MESURE, "[Expression]": expression}]}]}]}))
    out = client.list_measures("d1", workspace_id="w1")
    assert calls[0]["json"]["queries"] == [{"query": "EVALUATE INFO.VIEW.MEASURES()"}]
    assert out["expression_visible"] is visible
    assert out["measures"][0] == {"Name": "CA", "Table": "Ventes",
                                  "Description": "Chiffre d'affaires",
                                  "Expression": expression, "DisplayFolder": "Finance",
                                  "FormatString": "#,0", "DataType": "Decimal",
                                  "IsHidden": False, "State": "Ready"}


def test_des_mesures_refusees_le_disent(calls, client):
    calls.responses.append(_Resp({"results": [{"error": {"message": "not supported"}}]}))
    with pytest.raises(ValueError, match="not supported"):
        client.list_measures("d1")


def test_les_scopes_power_bi_ne_se_confondent_pas_avec_graph():
    assert scopes.short(scopes.POWERBI) == {
        "https://analysis.windows.net/powerbi/api/Workspace.Read.All",
        "https://analysis.windows.net/powerbi/api/Report.Read.All",
        "https://analysis.windows.net/powerbi/api/Dataset.Read.All"}
    lu = scopes.normalize("https://analysis.windows.net/powerbi/api/dataset.read.all User.Read")
    assert lu == {"https://analysis.windows.net/powerbi/api/Dataset.Read.All", "User.Read"}


def test_le_lien_d_approbation_garde_l_uri_power_bi():
    url = ms_auth.admin_consent_url(CID, REDIRECT, "st", scopes=scopes.POWERBI + ("Files.Read.All",))
    from urllib.parse import parse_qs, urlparse
    sent = parse_qs(urlparse(url).query)["scope"][0].split()
    assert sent == list(scopes.POWERBI) + ["https://graph.microsoft.com/Files.Read.All"]

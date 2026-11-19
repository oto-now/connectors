"""Le paquet Custom API (`oto.connectors.custom_api`) hors des cas communs : bornes, adresses, noms, une définition
reprise d'oto 1 exécutée par le moteur, la lecture d'un document OpenAPI par son adresse, le rapport à blanc et la garde
du préfixe réservé dans la fabrique.

Toutes les données sont fictives : outils inventés, domaines réservés aux tests.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest
import yaml

import engine_fake
import files_fake
from fabrique.descriptions import CONNECTORS, DescriptionError, check
from oto.connectors.custom_api import OpenAPIRefused, from_openapi, from_oto1, read_url, validate
from oto.connectors.custom_api import rapport
from oto.connectors.custom_api.naming import snake_case
from oto.connectors.custom_api.profile import MAX_FUNCTIONS, internal_host
from oto.tools.common import url_guard

CASES = yaml.safe_load((CONNECTORS / "_cases" / "custom_api.yaml").read_text(encoding="utf-8"))
TICKETS = next(case["document"] for case in CASES["valid"] if case["name"] == "acme_tickets")
OTO1 = {case["name"]: case["application"] for case in CASES["oto1"]}


def _codes(document) -> list[str]:
    return sorted({problem.code for problem in validate(document)})


def test_not_an_object():
    assert _codes(["connector"]) == ["custom_api_schema_invalid"]


def test_at_most_100_functions():
    document = copy.deepcopy(TICKETS)
    first = document["functions"][0]
    document["functions"] = [{**first, "name": f"read_{i}"} for i in range(MAX_FUNCTIONS + 1)]
    document["connector"].pop("probe")
    assert _codes(document) == ["custom_api_too_large"]


def test_at_most_256_kb():
    document = copy.deepcopy(TICKETS)
    document["functions"][0]["description"] = "x" * 1000
    document["functions"][0]["input"]["properties"] = {
        f"p{i}": {"type": "string", "description": "y" * 3000} for i in range(90)
    }
    assert "custom_api_too_large" in _codes(document)


@pytest.mark.parametrize(
    "host, internal",
    [
        ("api.example.com", False),
        ("8.8.8.8", False),
        ("127.0.0.1", True),
        ("10.0.0.7", True),
        ("169.254.169.254", True),
        ("[::1]", True),
        ("localhost", True),
        ("intranet", True),
        ("svc.cluster.local", True),
        ("db.internal", True),
        ("printer.home.arpa", True),
    ],
)
def test_internal_host(host, internal):
    assert internal_host(host) is internal


@pytest.mark.parametrize(
    "text, expected",
    [
        ("organizationId", "organization_id"),
        ("page[size]", "page_size"),
        ("HTTPServer", "http_server"),
        ("X-Api-Version", "x_api_version"),
        ("Acme Payroll", "acme_payroll"),
        ("--", ""),
    ],
)
def test_snake_case(text, expected):
    assert snake_case(text) == expected


def test_a_definition_taken_from_oto1_runs_on_the_engine():
    """La définition reprise s'exécute telle quelle : la variable est dans le chemin, l'argument renommé part sous le
    nom de l'API, la clé sous son en-tête, l'en-tête constant avec elle, et `output.items` déballe la liste."""
    application = copy.deepcopy(OTO1["converted_whole"])
    application["definition"]["headers"] = {"Acme-Version": "2"}
    application["definition"]["operations"][0]["output"] = {"items": "data"}
    document, report = from_oto1(application)
    assert report["status"] == "converted", report["blocking"]
    result, vendor = engine_fake.play(
        document,
        "list_collaborators",
        {"full_name": "Ada"},
        engine_fake.answer(200, {"data": [{"id": "c_1"}]}),
        credential={"token": "k-1"},
    )
    assert result["items"] == [{"id": "c_1"}]
    sent = engine_fake.sent(vendor.requests[0], document["connector"]["base_url"])
    assert sent["path"] == "/organizations/org_1/collaborators"
    assert sent["query"] == [["fullName", "Ada"]]
    assert sent["headers"]["x-acme-key"] == "k-1" and sent["headers"]["acme-version"] == "2"


def test_a_variable_that_is_not_one_path_segment_blocks():
    application = copy.deepcopy(OTO1["converted_whole"])
    application["definition"]["variables"]["organizationId"]["value"] = "../admin"
    document, report = from_oto1(application)
    assert document is None
    assert "custom_api_variable_invalid" in {problem["code"] for problem in report["blocking"]}


@pytest.mark.parametrize("key, value", [("description", "x" * 2001), ("logo_domain", "https://www.acme-payroll.test/")])
def test_a_presentation_the_format_refuses_is_left_behind(key, value):
    application = copy.deepcopy(OTO1["converted_whole"])
    application["definition"][key] = value
    document, report = from_oto1(application)
    assert report["status"] == "converted" and key not in document["connector"]
    assert f"definition.{key}" in {entry["path"] for entry in report["dropped"]}


def test_the_report_never_repeats_a_secret():
    document, report = from_oto1(OTO1["secret_in_the_export"])
    assert document is None and "made-up" not in json.dumps(report)


def test_openapi_beyond_100_operations():
    paths = {f"/items/{i}": {"get": {"operationId": f"item_{i}"}} for i in range(MAX_FUNCTIONS + 2)}
    result = from_openapi({"openapi": "3.0.0", "info": {"title": "Big"}, "paths": paths})
    assert len(result["candidates"]) == MAX_FUNCTIONS
    assert [s["code"] for s in result["skipped"]] == ["openapi_too_many_operations"] * 2


def test_openapi_larger_than_5_mb():
    with pytest.raises(OpenAPIRefused) as refused:
        from_openapi("openapi: 3.0.0\n" + "#" * (5 * 1024 * 1024))
    assert refused.value.code == "openapi_too_large"


def test_read_url_follows_no_redirect(monkeypatch):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", files_fake.resolve_publicly)
    vendor = engine_fake.Vendor(engine_fake.answer(302, headers={"Location": "https://elsewhere.example.com/x"}))
    with pytest.raises(OpenAPIRefused) as refused:
        read_url("https://docs.example.com/openapi.yaml", session=engine_fake.session(vendor))
    assert refused.value.code == "openapi_not_a_document"


def test_read_url_reads_the_document(monkeypatch):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", files_fake.resolve_publicly)
    vendor = engine_fake.Vendor(engine_fake.answer(200, text="openapi: 3.0.0\npaths: {}\n"))
    text = read_url("https://docs.example.com/openapi.yaml", session=engine_fake.session(vendor))
    assert from_openapi(text)["candidates"] == []


def test_read_url_refuses_an_internal_address():
    with pytest.raises(OpenAPIRefused):
        read_url("https://127.0.0.1/openapi.json")


def _export(tmp_path: pathlib.Path) -> pathlib.Path:
    applications = [
        {"id": f"app-{i}", "source": "api_apps" if i % 2 else "card", **OTO1[name]}
        for i, name in enumerate(["converted_whole", "session_mode", "key_in_header_with_variables"])
    ]
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"applications": applications}), encoding="utf-8")
    return path


def test_dry_run_report(tmp_path):
    output = tmp_path / "report.json"
    assert rapport.main([str(_export(tmp_path)), str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert (report["applications"], report["converted"], report["blocked"]) == (3, 1, 2)
    assert report["blocking_codes"] == {"custom_api_auth_not_supported": 1, "custom_api_example_missing": 1}
    assert [entry["status"] for entry in report["by_application"]] == ["converted", "blocked", "blocked"]
    assert report["by_application"][0]["functions"] == 2
    assert sorted(path.name for path in tmp_path.iterdir()) == ["export.json", "report.json"]


def test_dry_run_never_overwrites(tmp_path):
    output = tmp_path / "report.json"
    output.write_text("before", encoding="utf-8")
    assert rapport.main([str(_export(tmp_path)), str(output)]) == 2
    assert output.read_text(encoding="utf-8") == "before"


def test_dry_run_refuses_an_export_of_another_form(tmp_path):
    export = tmp_path / "export.json"
    export.write_text(json.dumps({"applications": [{"id": 1, "source": "api_apps", "org": "x"}]}), encoding="utf-8")
    assert rapport.main([str(export), str(tmp_path / "report.json")]) == 2
    assert not (tmp_path / "report.json").exists()


def test_no_shared_connector_bears_the_reserved_prefix(tmp_path):
    data = yaml.safe_load((CONNECTORS / "serper" / "connector.yaml").read_text(encoding="utf-8"))
    data["connector"]["name"] = "custom_serper"
    path = tmp_path / "custom_serper" / "connector.yaml"
    with pytest.raises(DescriptionError, match="reserved"):
        check(data, path)

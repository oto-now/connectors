"""NextmotionClient — verrouille le contrat HTTP construit par le client.

Mocke `requests.Session.request` : verbe + URL + params + corps, sans réseau ni
clé réelle. Cible ce qui pourrait dériver en silence : l'en-tête d'auth, la
surface (aucune méthode vers un endpoint médical, ni suppression d'un patient,
d'une facture ou d'un paiement), les chemins et filtres, le verbe et le corps de
chaque écriture, les bornes de pagination, le refus d'un identifiant qui n'est
pas un UUID ou d'un corps qui n'est pas un objet, et le décodage d'une réponse
204 / d'une erreur.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.nextmotion import NextmotionClient

BASE = "https://api.nextmotion.net/open_api"
A = "00000000-0000-4000-8000-00000000000a"
B = "00000000-0000-4000-8000-00000000000b"


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Seen(list):
    """The captured calls, plus `responses`: queued replies, served in order."""


@pytest.fixture
def calls(monkeypatch):
    seen = _Seen()
    responses = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, "headers": dict(self.headers), **kwargs})
        return responses.pop(0) if responses else _Resp({"data": []})

    monkeypatch.setattr("requests.Session.request", _request)
    seen.responses = responses
    return seen


@pytest.fixture
def client():
    return NextmotionClient(api_key="nm-test")


#: La surface entière : l'administratif en lecture et en écriture, plus l'identité du
#: patient. Aucune méthode ne vise un antécédent, une photo, une ordonnance, un
#: consentement signé, un soin, une consultation, une visite ou le chat.
METHODS = {
    "get_me", "list_clinics", "list_clinic_features", "list_doctors", "get_doctor",
    "create_doctor", "update_doctor", "delete_doctor",
    "list_appointments", "get_appointment", "list_appointment_billed_counts",
    "reschedule_appointment",
    "update_appointment", "delete_appointment", "search_time_slots",
    "list_appointment_requests", "get_appointment_request", "create_appointment_request",
    "list_calendar_journeys",
    "list_calendar_absences", "get_calendar_absence", "create_calendar_absence",
    "update_calendar_absence", "delete_calendar_absence",
    "list_calendar_opening_hours", "get_calendar_opening_hour",
    "create_calendar_opening_hour", "update_calendar_opening_hour",
    "delete_calendar_opening_hour",
    "list_appointment_rooms", "get_appointment_room", "create_appointment_room",
    "update_appointment_room", "delete_appointment_room",
    "list_appointment_devices", "get_appointment_device", "create_appointment_device",
    "update_appointment_device", "delete_appointment_device",
    "list_visit_types", "get_visit_type", "create_visit_type", "update_visit_type",
    "delete_visit_type", "reorder_visit_types",
    "list_visit_type_categories", "get_visit_type_category",
    "create_visit_type_category", "update_visit_type_category",
    "delete_visit_type_category", "reorder_visit_type_categories",
    "list_sub_visit_types", "get_sub_visit_type",
    "list_treatment_types", "get_treatment_type", "create_treatment_type",
    "update_treatment_type", "delete_treatment_type",
    "get_post_treatment_config", "update_post_treatment_config",
    "list_treatment_pricings", "get_treatment_pricing",
    "list_treatment_pricing_distributions", "set_treatment_pricing_distributions",
    "list_treatment_packages", "get_treatment_package", "create_treatment_package",
    "update_treatment_package", "delete_treatment_package",
    "list_treatment_package_items", "create_treatment_package_item",
    "replace_treatment_package_items", "update_treatment_package_item",
    "delete_treatment_package_item",
    "list_treatment_package_distributions", "set_treatment_package_distributions",
    "list_accounting_distributions", "get_accounting_distribution",
    "create_accounting_distribution", "update_accounting_distribution",
    "delete_accounting_distribution",
    "list_global_products",
    "list_quotes", "get_quote", "update_quote", "delete_quote", "validate_quote",
    "list_invoices", "get_invoice", "update_invoice", "validate_invoice", "pay_invoice",
    "create_credit_note",
    "list_payments", "get_payment", "update_payment", "get_treatment_provider",
    "list_payment_mediums", "get_payment_medium", "create_payment_medium",
    "update_payment_medium", "delete_payment_medium",
    "get_appointment_income_statistics", "list_treatment_type_statistics",
    "list_treatment_type_income_statistics", "get_patient_stats",
    "list_products", "get_product", "create_product", "update_product", "delete_product",
    "list_leads", "get_lead", "create_lead", "update_lead", "delete_lead",
    "convert_lead_to_patient", "create_call", "create_communication_record",
    "list_object_labels",
    "list_communication_templates", "get_communication_template",
    "update_communication_template", "list_communication_template_placeholders",
    "list_document_templates", "get_document_template", "create_document_template",
    "update_document_template", "delete_document_template",
    "duplicate_document_template", "list_document_template_placeholders",
    "list_survey_forms", "get_survey_form", "create_survey_form", "update_survey_form",
    "delete_survey_form", "list_survey_form_placeholders",
    "list_webhooks", "get_webhook", "create_webhook", "update_webhook", "delete_webhook",
    "list_patients", "get_patient", "create_patient", "update_patient",
}

_MEDICAL_MARKERS = ("patient", "medical", "prescription", "consent", "media",
                    "photo", "consultation", "treatments", "visit_record", "survey",
                    "chat", "contact", "extract")

#: Par patient, voulu : des totaux financiers et des dates de visite, par id.
_PER_PATIENT_ADMIN = {"get_patient_stats"}
#: L'IDENTITÉ du patient, voulue (décision du propriétaire) : liste, fiche, création,
#: modification, et la conversion d'un lead en patient. Jamais son dossier médical.
_PATIENT_IDENTITY = {"list_patients", "get_patient", "create_patient", "update_patient",
                     "convert_lead_to_patient"}
#: Les MODÈLES de questionnaires (notes BoltNote, consentements de soin) : de la
#: configuration de la clinique, jamais la réponse d'un patient.
_SURVEY_TEMPLATES = {"list_survey_forms", "get_survey_form", "create_survey_form",
                     "update_survey_form", "delete_survey_form",
                     "list_survey_form_placeholders"}
#: Les suppressions que le périmètre exclut : un patient, une pièce comptable.
_EXCLUDED = {"delete_patient", "delete_invoice", "delete_payment",
             "extract_treatment_package", "create_quote", "create_invoice"}


def test_surface_is_exactly_the_administrative_scope():
    public = {n for n in dir(NextmotionClient)
              if not n.startswith("_") and callable(getattr(NextmotionClient, n))}
    assert public == METHODS


def test_no_method_targets_a_medical_resource():
    for name in METHODS - _PER_PATIENT_ADMIN - _PATIENT_IDENTITY - _SURVEY_TEMPLATES:
        assert not any(m in name for m in _MEDICAL_MARKERS), name


def test_excluded_deletions_and_consultation_writes_have_no_method():
    assert not _EXCLUDED & set(dir(NextmotionClient))


def test_api_key_goes_in_bearer_header_never_in_params(calls, client):
    client.get_me()
    call = calls[0]
    assert call["headers"]["Authorization"] == "Bearer nm-test"
    assert "nm-test" not in json.dumps(call.get("params") or {})
    assert call["url"] == f"{BASE}/v4/users/me"


@pytest.mark.parametrize("fn,args,kwargs,method,path,params", [
    ("list_clinics", (), {}, "GET", "/v4/clinics", {"limit": 50, "offset": 0}),
    ("list_doctors", (A,), {"limit": 10, "offset": 20}, "GET",
     f"/v4/clinics/{A}/doctors", {"limit": 10, "offset": 20}),
    ("get_doctor", (A,), {}, "GET", f"/v4/doctors/{A}", {}),
    ("list_appointments", (A,), {"date": "2026-01-02", "patient_id": B}, "GET",
     f"/v4/clinics/{A}/calendar_appointments",
     {"limit": 50, "offset": 0, "date": "2026-01-02", "patient": B}),
    ("get_appointment", (A,), {}, "GET", f"/v4/calendar_appointments/{A}", {}),
    ("delete_appointment", (A,), {}, "DELETE", f"/v4/calendar_appointments/{A}", {}),
    ("list_visit_types", (A,), {"visit_type_category_id": B}, "GET",
     f"/v4/clinics/{A}/visit_types",
     {"limit": 50, "offset": 0, "visit_type_category": B}),
    ("get_visit_type", (A,), {}, "GET", f"/v4/visit_types/{A}", {}),
    ("list_visit_type_categories", (A,), {}, "GET",
     f"/v4/clinics/{A}/visit_type_categories", {"limit": 50, "offset": 0}),
    ("get_visit_type_category", (A,), {}, "GET", f"/v4/visit_type_categories/{A}", {}),
    ("list_sub_visit_types", (A,), {"visit_type_id": B}, "GET",
     f"/v4/clinics/{A}/sub_visit_types", {"limit": 50, "offset": 0, "visit_type": B}),
    ("get_sub_visit_type", (A,), {}, "GET", f"/v4/sub_visit_types/{A}", {}),
    ("list_treatment_types", (A,), {"search": "zzz"}, "GET",
     f"/v4/clinics/{A}/treatment_types", {"limit": 50, "offset": 0, "search": "zzz"}),
    ("get_treatment_type", (A,), {}, "GET", f"/v4/treatment_types/{A}", {}),
    ("list_treatment_pricings", (A,), {"treatment_type_id": B}, "GET",
     f"/v4/clinics/{A}/treatment_pricings",
     {"limit": 50, "offset": 0, "treatment_type": B}),
    ("get_treatment_pricing", (A,), {}, "GET", f"/v4/treatment_pricings/{A}", {}),
    ("list_quotes", (A,), {"patient_id": B}, "GET", f"/v4/clinics/{A}/quotes",
     {"limit": 50, "offset": 0, "patient": B}),
    ("get_quote", (A,), {}, "GET", f"/v4/quotes/{A}", {}),
    ("list_invoices", (A,), {}, "GET", f"/v4/clinics/{A}/invoices",
     {"limit": 50, "offset": 0}),
    ("get_invoice", (A,), {}, "GET", f"/v4/invoices/{A}", {}),
    ("list_products", (A,), {"search": "zzz", "stock_state": "low",
                             "expiring_within_days": 30, "order": "-stock_level"},
     "GET", f"/v4/clinics/{A}/products",
     {"limit": 50, "offset": 0, "search": "zzz", "stock_state": "low",
      "expiring_within_days": 30, "order": "-stock_level"}),
    ("list_products", (A,), {}, "GET", f"/v4/clinics/{A}/products",
     {"limit": 50, "offset": 0}),
    ("get_product", (A,), {}, "GET", f"/v4/products/{A}", {}),
    ("list_clinic_features", (A,), {}, "GET", f"/v4/clinics/{A}/features",
     {"limit": 50, "offset": 0}),
    ("list_appointment_requests", (A,), {"status": "new"}, "GET",
     f"/v4/clinics/{A}/appointment_requests", {"limit": 50, "offset": 0, "status": "new"}),
    ("get_appointment_request", (A,), {}, "GET", f"/v4/appointment_requests/{A}", {}),
    ("list_calendar_journeys", (A,), {"start_date": "2026-01-01", "include_ongoing": False,
                                      "doctor_ids": [A, B], "patient_id": B,
                                      "order": "-start_time", "status": "finished"},
     "GET", f"/v4/clinics/{A}/calendar_journeys",
     {"limit": 50, "offset": 0, "start_date": "2026-01-01", "include_ongoing": "false",
      "doctor": [A, B], "patient": B, "order": "-start_time", "status": "finished"}),
    ("list_calendar_absences", (A,), {"end_date": "2026-01-31", "show_all": True}, "GET",
     f"/v4/clinics/{A}/calendar_absences",
     {"limit": 50, "offset": 0, "end_date": "2026-01-31", "show_all": "true"}),
    ("get_calendar_absence", (A,), {}, "GET", f"/v4/calendar_absences/{A}", {}),
    ("list_calendar_opening_hours", (A,), {}, "GET",
     f"/v4/clinics/{A}/calendar_opening_hours", {"limit": 50, "offset": 0}),
    ("get_calendar_opening_hour", (A,), {}, "GET", f"/v4/calendar_opening_hours/{A}", {}),
    ("list_appointment_rooms", (A,), {}, "GET", f"/v4/clinics/{A}/appointment_rooms",
     {"limit": 50, "offset": 0}),
    ("get_appointment_room", (A,), {}, "GET", f"/v4/appointment_rooms/{A}", {}),
    ("list_appointment_devices", (A,), {}, "GET", f"/v4/clinics/{A}/appointment_devices",
     {"limit": 50, "offset": 0}),
    ("get_appointment_device", (A,), {}, "GET", f"/v4/appointment_devices/{A}", {}),
    ("list_treatment_pricing_distributions", (A,), {}, "GET",
     f"/v4/treatment_pricings/{A}/user_accounting_distributions", {}),
    ("list_treatment_packages", (A,), {"search": "zzz"}, "GET",
     f"/v4/clinics/{A}/treatment_packages", {"limit": 50, "offset": 0, "search": "zzz"}),
    ("get_treatment_package", (A,), {}, "GET", f"/v4/treatment_packages/{A}", {}),
    ("list_treatment_package_items", (A,), {"limit": 5}, "GET",
     f"/v4/treatment_packages/{A}/items", {"limit": 5, "offset": 0}),
    ("list_treatment_package_distributions", (A,), {}, "GET",
     f"/v4/treatment_packages/{A}/user_accounting_distributions", {}),
    ("list_accounting_distributions", (A,), {}, "GET",
     f"/v4/clinics/{A}/accounting_distributions", {"limit": 50, "offset": 0}),
    ("get_accounting_distribution", (A,), {}, "GET", f"/v4/accounting_distributions/{A}",
     {}),
    ("list_global_products", (A,), {"search": "zzz"}, "GET",
     f"/v4/clinics/{A}/global_products", {"limit": 50, "offset": 0, "search": "zzz"}),
    ("list_payments", (A,), {"invoice_id": B}, "GET", f"/v4/clinics/{A}/payments",
     {"limit": 50, "offset": 0, "invoice": B}),
    ("list_payments", (A,), {"paid_from": "2026-10-01", "paid_to": "2026-10-31",
                             "limit": 100}, "GET", f"/v4/clinics/{A}/payments",
     {"limit": 100, "offset": 0, "payment_time__gte": "2026-10-01",
      "payment_time__lte": "2026-10-31"}),
    ("get_payment", (A,), {}, "GET", f"/v4/payments/{A}", {}),
    ("list_payment_mediums", (A,), {}, "GET", f"/v4/clinics/{A}/payment_mediums",
     {"limit": 50, "offset": 0}),
    ("get_payment_medium", (A,), {}, "GET", f"/v4/payment_mediums/{A}", {}),
    ("get_appointment_income_statistics", (A,),
     {"start_date": "2026-01-01", "end_date": "2026-06-30", "period_type": "week"}, "GET",
     f"/v4/clinics/{A}/statistics/appointment_income",
     {"start_time": "2026-01-01", "end_time": "2026-06-30", "period_type": "week"}),
    ("list_treatment_type_statistics", (A,), {}, "GET",
     f"/v4/clinics/{A}/statistics/treatment_types", {}),
    ("list_treatment_type_income_statistics", (A,), {"period_type": "year"}, "GET",
     f"/v4/clinics/{A}/statistics/treatment_types/income", {"period_type": "year"}),
    ("get_patient_stats", (A,), {}, "GET", f"/v4/patients/{A}/stats", {}),
    ("list_leads", (A,), {}, "GET", f"/v4/clinics/{A}/leads", {"limit": 50, "offset": 0}),
    ("get_lead", (A,), {}, "GET", f"/v4/leads/{A}", {}),
    ("list_object_labels", (A,), {"types": ["lead_source", "quote_tag"]}, "GET",
     f"/v4/clinics/{A}/object_labels",
     {"limit": 50, "offset": 0, "type": ["lead_source", "quote_tag"]}),
    ("list_communication_templates", (A,), {"kind": "sms"}, "GET",
     f"/v4/clinics/{A}/communication_templates", {"limit": 50, "offset": 0, "kind": "sms"}),
    ("get_communication_template", (A,), {}, "GET", f"/v4/communication_templates/{A}",
     {}),
    ("list_document_templates", (A,), {"type": 0, "master_id": B}, "GET",
     f"/v4/clinics/{A}/document_templates",
     {"limit": 50, "offset": 0, "type": 0, "master": B}),
    ("get_document_template", (A,), {}, "GET", f"/v4/document_templates/{A}", {}),
    ("list_webhooks", (A,), {}, "GET", f"/v4/clinics/{A}/webhooks",
     {"limit": 50, "offset": 0}),
    ("get_webhook", (A,), {}, "GET", f"/v4/webhooks/{A}", {}),
    ("list_patients", (A,), {"is_archived": False, "limit": 100}, "GET",
     f"/v4/clinics/{A}/patients", {"limit": 100, "offset": 0, "is_archived": "false"}),
    ("list_patients", (A,), {}, "GET", f"/v4/clinics/{A}/patients",
     {"limit": 50, "offset": 0}),
    ("list_patients", (A,), {"search": "zzz", "birth_date": "01-02",
                             "phone_number": "+0000", "invoice_total_gt": "100.00"},
     "GET", f"/v4/clinics/{A}/patients",
     {"limit": 50, "offset": 0, "search": "zzz", "birth_date": "01-02",
      "phone_number": "+0000", "invoice_total__gt": "100.00"}),
    ("get_patient", (A,), {}, "GET", f"/v4/patients/{A}", {}),
    ("get_post_treatment_config", (A,), {}, "GET",
     f"/v4/treatment_types/{A}/post_treatment_config", {}),
    ("list_survey_forms", (A,), {"search": "zzz", "type": "bolt_note"}, "GET",
     f"/v4/clinics/{A}/survey_forms",
     {"limit": 50, "offset": 0, "search": "zzz", "type": "bolt_note"}),
    ("get_survey_form", (A,), {}, "GET", f"/v4/survey_forms/{A}", {}),
])
def test_read_paths_and_params(calls, client, fn, args, kwargs, method, path, params):
    getattr(client, fn)(*args, **kwargs)
    call = calls[0]
    assert (call["method"], call["url"], call["params"]) == (method, f"{BASE}{path}", params)
    assert call["json"] is None




@pytest.mark.parametrize("fn,kwargs,path,params", [
    ("list_communication_template_placeholders", {"type": "quote"},
     "/v4/communication_templates/autocomplete", {"type": "quote"}),
    ("list_communication_template_placeholders", {},
     "/v4/communication_templates/autocomplete", {}),
    ("list_document_template_placeholders", {"type": 1},
     "/v4/document_templates/autocomplete", {"type": 1}),
    ("list_survey_form_placeholders", {"type": "treatment_consent"},
     "/v4/survey_forms/autocomplete", {"type": "treatment_consent"}),
])
def test_placeholder_reads_hang_under_no_clinic(calls, client, fn, kwargs, path, params):
    getattr(client, fn)(**kwargs)
    call = calls[0]
    assert (call["method"], call["url"], call["params"]) == ("GET", f"{BASE}{path}", params)


BODY = {"name": "Test", "color": None, "position": 0}
SENT = {"name": "Test", "position": 0}
ITEMS = [{"id": B, "extra": None}]


@pytest.mark.parametrize("fn,args,method,path", [
    ("create_doctor", (A,), "POST", f"/v4/clinics/{A}/doctors"),
    ("update_doctor", (A,), "PUT", f"/v4/doctors/{A}"),
    ("update_appointment", (A,), "PUT", f"/v4/calendar_appointments/{A}"),
    ("create_appointment_request", (), "POST", "/v4/appointment_requests"),
    ("create_calendar_absence", (A,), "POST", f"/v4/clinics/{A}/calendar_absences"),
    ("update_calendar_absence", (A,), "PUT", f"/v4/calendar_absences/{A}"),
    ("create_calendar_opening_hour", (A,), "POST",
     f"/v4/clinics/{A}/calendar_opening_hours"),
    ("update_calendar_opening_hour", (A,), "PUT", f"/v4/calendar_opening_hours/{A}"),
    ("create_appointment_room", (A,), "POST", f"/v4/clinics/{A}/appointment_rooms"),
    ("update_appointment_room", (A,), "PUT", f"/v4/appointment_rooms/{A}"),
    ("create_appointment_device", (A,), "POST", f"/v4/clinics/{A}/appointment_devices"),
    ("update_appointment_device", (A,), "PUT", f"/v4/appointment_devices/{A}"),
    ("create_visit_type", (A,), "POST", f"/v4/clinics/{A}/visit_types"),
    ("update_visit_type", (A,), "PUT", f"/v4/visit_types/{A}"),
    ("create_visit_type_category", (A,), "POST", f"/v4/clinics/{A}/visit_type_categories"),
    ("update_visit_type_category", (A,), "PUT", f"/v4/visit_type_categories/{A}"),
    ("create_treatment_type", (A,), "POST", f"/v4/clinics/{A}/treatment_types"),
    ("update_treatment_type", (A,), "PUT", f"/v4/treatment_types/{A}"),
    ("update_post_treatment_config", (A,), "PUT",
     f"/v4/treatment_types/{A}/post_treatment_config"),
    ("create_treatment_package", (A,), "POST", f"/v4/clinics/{A}/treatment_packages"),
    ("update_treatment_package", (A,), "PUT", f"/v4/treatment_packages/{A}"),
    ("create_treatment_package_item", (A,), "POST", f"/v4/treatment_packages/{A}/items"),
    ("update_treatment_package_item", (A,), "PUT", f"/v4/treatment_package_items/{A}"),
    ("create_accounting_distribution", (A,), "POST",
     f"/v4/clinics/{A}/accounting_distributions"),
    ("update_accounting_distribution", (A,), "PUT", f"/v4/accounting_distributions/{A}"),
    ("update_quote", (A,), "PUT", f"/v4/quotes/{A}"),
    ("validate_quote", (A,), "POST", f"/v4/quotes/{A}/validate"),
    ("update_invoice", (A,), "PUT", f"/v4/invoices/{A}"),
    ("validate_invoice", (A,), "POST", f"/v4/invoices/{A}/validate"),
    ("pay_invoice", (A,), "POST", f"/v4/invoices/{A}/pay"),
    ("create_credit_note", (A,), "POST", f"/v4/clinics/{A}/credit_notes"),
    ("update_payment", (A,), "PUT", f"/v4/payments/{A}"),
    ("create_payment_medium", (A,), "POST", f"/v4/clinics/{A}/payment_mediums"),
    ("update_payment_medium", (A,), "PUT", f"/v4/payment_mediums/{A}"),
    ("create_product", (A,), "POST", f"/v4/clinics/{A}/products"),
    ("update_product", (A,), "PUT", f"/v4/products/{A}"),
    ("create_lead", (A,), "POST", f"/v4/clinics/{A}/leads"),
    ("update_lead", (A,), "PUT", f"/v4/leads/{A}"),
    ("create_call", (A,), "POST", f"/v4/clinics/{A}/calls"),
    ("create_communication_record", (A,), "POST", f"/v4/clinics/{A}/communication_records"),
    ("update_communication_template", (A,), "PUT", f"/v4/communication_templates/{A}"),
    ("create_document_template", (A,), "POST", f"/v4/clinics/{A}/document_templates"),
    ("update_document_template", (A,), "PUT", f"/v4/document_templates/{A}"),
    ("create_survey_form", (A,), "POST", f"/v4/clinics/{A}/survey_forms"),
    ("update_survey_form", (A,), "PUT", f"/v4/survey_forms/{A}"),
    ("create_webhook", (A,), "POST", f"/v4/clinics/{A}/webhooks"),
    ("update_webhook", (A,), "PUT", f"/v4/webhooks/{A}"),
    ("create_patient", (A,), "POST", f"/v4/clinics/{A}/patients"),
    ("update_patient", (A,), "PUT", f"/v4/patients/{A}"),
])
def test_object_writes_send_the_cleaned_body(calls, client, fn, args, method, path):
    getattr(client, fn)(*args, body=dict(BODY))
    call = calls[0]
    assert (call["method"], call["url"], call["json"]) == (method, f"{BASE}{path}", SENT)
    assert not call["params"]


@pytest.mark.parametrize("fn,path", [
    ("reorder_visit_types", f"/v4/clinics/{A}/visit_types/reorder"),
    ("reorder_visit_type_categories", f"/v4/clinics/{A}/visit_type_categories/reorder"),
    ("replace_treatment_package_items", f"/v4/treatment_packages/{A}/items"),
    ("set_treatment_pricing_distributions",
     f"/v4/treatment_pricings/{A}/user_accounting_distributions"),
    ("set_treatment_package_distributions",
     f"/v4/treatment_packages/{A}/user_accounting_distributions"),
])
def test_array_writes_send_a_list_of_cleaned_objects(calls, client, fn, path):
    getattr(client, fn)(A, items=[dict(i) for i in ITEMS])
    call = calls[0]
    assert (call["method"], call["url"], call["json"]) == ("PUT", f"{BASE}{path}", [{"id": B}])


@pytest.mark.parametrize("fn,method,path", [
    ("delete_doctor", "DELETE", f"/v4/doctors/{A}"),
    ("delete_calendar_absence", "DELETE", f"/v4/calendar_absences/{A}"),
    ("delete_calendar_opening_hour", "DELETE", f"/v4/calendar_opening_hours/{A}"),
    ("delete_appointment_room", "DELETE", f"/v4/appointment_rooms/{A}"),
    ("delete_appointment_device", "DELETE", f"/v4/appointment_devices/{A}"),
    ("delete_visit_type", "DELETE", f"/v4/visit_types/{A}"),
    ("delete_visit_type_category", "DELETE", f"/v4/visit_type_categories/{A}"),
    ("delete_treatment_type", "DELETE", f"/v4/treatment_types/{A}"),
    ("delete_treatment_package", "DELETE", f"/v4/treatment_packages/{A}"),
    ("delete_treatment_package_item", "DELETE", f"/v4/treatment_package_items/{A}"),
    ("delete_accounting_distribution", "DELETE", f"/v4/accounting_distributions/{A}"),
    ("delete_quote", "DELETE", f"/v4/quotes/{A}"),
    ("delete_payment_medium", "DELETE", f"/v4/payment_mediums/{A}"),
    ("delete_product", "DELETE", f"/v4/products/{A}"),
    ("delete_lead", "DELETE", f"/v4/leads/{A}"),
    ("delete_document_template", "DELETE", f"/v4/document_templates/{A}"),
    ("delete_survey_form", "DELETE", f"/v4/survey_forms/{A}"),
    ("delete_webhook", "DELETE", f"/v4/webhooks/{A}"),
    ("convert_lead_to_patient", "POST", f"/v4/leads/{A}/convert_to_patient"),
    ("duplicate_document_template", "POST", f"/v4/document_templates/{A}/duplicate"),
])
def test_bodiless_writes(calls, client, fn, method, path):
    getattr(client, fn)(A)
    call = calls[0]
    assert (call["method"], call["url"], call["json"]) == (method, f"{BASE}{path}", None)


@pytest.mark.parametrize("fn", ["validate_quote", "validate_invoice"])
def test_validation_without_body_sends_an_empty_object(calls, client, fn):
    getattr(client, fn)(A)
    assert calls[0]["json"] == {}


@pytest.mark.parametrize("fn,args,kwargs", [
    ("update_lead", (A,), {"body": [{"first_name": "x"}]}),
    ("create_patient", (A,), {"body": "x"}),
    ("create_appointment_request", (), {"body": None}),
    ("reorder_visit_types", (A,), {"items": {"id": B}}),
    ("set_treatment_package_distributions", (A,), {"items": ["x"]}),
    ("update_patient", ("../x",), {"body": {}}),
    ("list_survey_form_placeholders", (), {"type": ""}),
])
def test_bad_bodies_and_ids_are_refused_before_the_request(calls, client, fn, args, kwargs):
    with pytest.raises(ValueError):
        getattr(client, fn)(*args, **kwargs)
    assert not calls
@pytest.mark.parametrize("fn,kwargs,match", [
    ("list_calendar_journeys", {"order": "patient_name"}, "order"),
    ("list_calendar_journeys", {"order": "-patient_name"}, "order"),
    ("list_calendar_journeys", {"doctor_ids": A}, "must be a list"),
    ("list_calendar_journeys", {"visit_type_ids": ["42"]}, "UUID"),
    ("list_object_labels", {"types": "lead_source"}, "must be a list"),
    ("get_appointment_income_statistics", {"period_type": "quarter"}, "period_type"),
    ("list_payments", {"invoice_id": "../x"}, "UUID"),
])
def test_bad_filters_are_refused_before_the_request(calls, client, fn, kwargs, match):
    with pytest.raises(ValueError, match=match):
        getattr(client, fn)(A, **kwargs)
    assert not calls


def test_no_name_search_is_exposed_where_the_api_matches_on_a_person():
    import inspect

    for fn in ("list_leads", "list_calendar_journeys"):
        assert "search" not in inspect.signature(getattr(NextmotionClient, fn)).parameters


def test_patient_list_exposes_the_spec_look_up_filters_and_nothing_else():
    import inspect

    params = set(inspect.signature(NextmotionClient.list_patients).parameters)
    assert params == {"self", "clinic_id", "search", "birth_date", "phone_number",
                      "invoice_total_gt", "is_archived", "limit", "offset"}


def test_omitted_filters_are_not_sent(calls, client):
    client.list_appointments(A)
    assert calls[0]["params"] == {"limit": 50, "offset": 0}


def test_reschedule_posts_both_required_fields(calls, client):
    client.reschedule_appointment(A, visit_type_opening_hour_id=B,
                                  time_slot="2026-01-02T10:00:00+01:00")
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{BASE}/v4/calendar_appointments/{A}/reschedule"
    assert call["json"] == {"visit_type_opening_hour": B,
                            "time_slot": "2026-01-02T10:00:00+01:00"}


def test_reschedule_refuses_an_empty_time_slot(calls, client):
    with pytest.raises(ValueError, match="time_slot"):
        client.reschedule_appointment(A, visit_type_opening_hour_id=B, time_slot="")
    assert not calls


def test_time_slot_search_is_a_post_with_only_given_fields(calls, client):
    client.search_time_slots(A, start_date="2026-01-01", doctor_id=B)
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{BASE}/v4/clinics/{A}/visit_types/opening_hours"
    assert call["json"] == {"start_date": "2026-01-01", "doctor_id": B}


@pytest.mark.parametrize("bad", ["", None, "42", f"{A}/../x", "../users/me", f"{A}?x=1", f"{A}\n"])
def test_a_non_uuid_identifier_never_reaches_the_url(calls, client, bad):
    with pytest.raises(ValueError, match="UUID"):
        client.delete_appointment(bad)
    with pytest.raises(ValueError, match="UUID"):
        client.list_appointments(A, patient_id=bad if bad is not None else "")
    assert not calls


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (50, -1)])
def test_pagination_bounds(calls, client, limit, offset):
    with pytest.raises(ValueError):
        client.list_clinics(limit=limit, offset=offset)
    assert not calls


def test_204_returns_none(calls, client):
    calls.responses.append(_Resp(None, status_code=204))
    assert client.delete_appointment(A) is None


def test_error_envelope_raises_with_status_code(calls, client):
    calls.responses.append(_Resp(
        {"errors": [{"code": "non_employee_access_denied", "message": "x"}]},
        status_code=403))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.list_doctors(A)
    assert exc.value.status_code == 403


# --- le décompte d'un forfait : la seule lecture d'un soin, réduite à la source ------

def test_billed_counts_keep_only_counters_and_ids(calls, client):
    calls.responses.append(_Resp({"count": 1, "next": None, "previous": None, "data": [{
        "id": A, "treatment_type": B, "treatment_pricing": B, "quantity": 1,
        "treatment_time": "2026-10-02T10:00:00Z", "quote": None, "invoice": A,
        "notes": "NOTE CLINIQUE", "zone": "ZONE", "text": "TEXTE", "consultation": B,
        "consent_form": B, "provider": {"last_name": "X"}, "pricing": "90.00"}]}))
    out = client.list_appointment_billed_counts(A, billed="invoiced", limit=100)
    assert calls[0]["url"] == f"{BASE}/v4/calendar_appointments/{A}/treatments"
    assert calls[0]["params"] == {"limit": 100, "offset": 0, "type": "invoiced"}
    assert out["data"] == [{"id": A, "treatment_type": B, "treatment_pricing": B,
                            "quantity": 1, "treatment_time": "2026-10-02T10:00:00Z",
                            "quote": None, "invoice": A}]
    assert "NOTE" not in json.dumps(out) and "ZONE" not in json.dumps(out)


def test_billed_counts_refuse_an_unknown_filter_and_an_unexpected_answer(calls, client):
    with pytest.raises(ValueError, match="billed"):
        client.list_appointment_billed_counts(A, billed="all")
    assert not calls
    calls.responses.append(_Resp({"detail": "x"}))
    with pytest.raises(ValueError, match="data"):
        client.list_appointment_billed_counts(A)


# --- les encaissements par praticien : la période côté API, le praticien réduit ------

#: Un soin tel que l'API le rend (champs du schéma OpenAPI, valeurs inventées) : du
#: contenu médical, un patient par sa consultation, et un praticien complet.
_TREATMENT = {
    "id": A, "created_time": "2026-10-02T09:00:00Z", "modified_time": "2026-10-02T09:00:00Z",
    "consultation": B, "treatment_type": B, "treatment_pricing": B, "pricing": "90.00",
    "quantity": 1, "treatment_time": "2026-10-02T10:00:00Z", "notes": "NOTE CLINIQUE",
    "zone": "ZONE TRAITEE", "text": "TEXTE LIBRE", "quote": None, "consent_form": B,
    "invoice": B, "is_copilot_ai": False,
    "secretary": {"id": B, "first_name": "Secretaire", "last_name": "Fictive"},
    "assistant": {"id": B, "first_name": "Assistant", "last_name": "Fictif"},
    "provider": {
        "id": A, "user_id": B, "first_name": "Praticien", "last_name": "Fictif",
        "email": "praticien@example.test", "preferred_language": "fr",
        "photograph": {"media_file": "https://files.example.test/p.jpg"},
        "preview_photograph": {"media_file": "https://files.example.test/pp.jpg"},
        "speciality": "SPECIALITE", "kind": 1, "color": "#000000",
        "clinic": {"id": B, "name": "Clinique Fictive"}, "permissions": ["x"],
        "id_no_1": "NUMERO-PRO-1", "id_no_2": "NUMERO-PRO-2", "id_no_3": "NUMERO-PRO-3",
        "visit_type_category": {"id": B}, "visit_types": [B], "is_active": "true",
        "is_disabled": False, "is_admin": False, "is_assistant": False,
        "has_copilot_ai_access": False, "display_in_calendar_provider_view": True,
        "activate_online_booking": True, "display_in_appointment_page": True,
        "display_in_patient_ownership_dropdown": True,
    },
}


def test_treatment_provider_keeps_only_the_practitioner_id_and_name(calls, client):
    calls.responses.append(_Resp({"data": _TREATMENT}))
    out = client.get_treatment_provider(A)
    assert (calls[0]["method"], calls[0]["url"]) == ("GET", f"{BASE}/v4/treatments/{A}")
    assert out == {"data": {"id": A, "invoice": B, "provider": {
        "id": A, "first_name": "Praticien", "last_name": "Fictif"}}}
    text = json.dumps(out)
    for leaked in ("NOTE", "ZONE", "TEXTE", "SPECIALITE", "NUMERO-PRO", "example.test",
                   "Clinique", "Secretaire", "Assistant", "90.00", "2026-10-02"):
        assert leaked not in text, leaked


def test_treatment_provider_is_an_allow_list(calls, client):
    """A field Nextmotion would add, to the treatment or to its provider, never goes out."""
    added = {**_TREATMENT, "diagnosis": "NOUVEAU",
             "provider": {**_TREATMENT["provider"], "phone_number": "NOUVEAU"}}
    calls.responses.append(_Resp({"data": added}))
    out = client.get_treatment_provider(A)
    assert set(out["data"]) == {"id", "invoice", "provider"}
    assert set(out["data"]["provider"]) == {"id", "first_name", "last_name"}
    assert "NOUVEAU" not in json.dumps(out)


def test_treatment_without_provider_says_none(calls, client):
    calls.responses.append(_Resp({"data": {**_TREATMENT, "provider": None}}))
    assert client.get_treatment_provider(A)["data"]["provider"] is None


@pytest.mark.parametrize("answer", [{"detail": "x"}, {"data": [_TREATMENT]},
                                    {"data": {**_TREATMENT, "provider": "x"}}])
def test_treatment_provider_refuses_an_unexpected_answer(calls, client, answer):
    calls.responses.append(_Resp(answer))
    with pytest.raises(ValueError, match="unexpected"):
        client.get_treatment_provider(A)


def test_treatment_provider_refuses_a_non_uuid_before_the_request(calls, client):
    with pytest.raises(ValueError, match="UUID"):
        client.get_treatment_provider("../x")
    assert not calls


# --- la description suit le client sur list_payments ----------------------------------

def _description():
    import pathlib

    import yaml

    path = pathlib.Path(__file__).parents[1] / "connectors" / "nextmotion" / "connector.yaml"
    return yaml.safe_load(path.read_text())


def test_description_of_list_payments_follows_the_client(calls, client):
    import inspect

    fn = next(f for f in _description()["functions"] if f["name"] == "list_payments")
    params = set(inspect.signature(NextmotionClient.list_payments).parameters) - {"self"}
    assert set(fn["input"]["properties"]) == params
    assert fn["call"]["query"] == {"invoice": "invoice_id", "payment_time__gte": "paid_from",
                                   "payment_time__lte": "paid_to", "limit": "limit",
                                   "offset": "offset"}
    args = {"invoice_id": B, "paid_from": "2026-10-01", "paid_to": "2026-10-31",
            "limit": 20, "offset": 40}
    client.list_payments(A, **args)
    assert calls[0]["params"] == {api: args[arg] for api, arg in fn["call"]["query"].items()}
    # The answer of the API passes as it is: no key added nor removed by the description.
    assert "output" not in fn


def test_no_described_function_reads_a_treatment_record():
    """The practitioner lookup stays in the client: the format could only reduce the
    treatment record by a deny-list (strip), which would let a new field through."""
    for fn in _description()["functions"]:
        path = fn.get("call", {}).get("path", "")
        assert not path.startswith("/v4/treatments/"), fn["name"]

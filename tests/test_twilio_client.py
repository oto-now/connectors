"""Contrat du client Twilio (REST v2010, Basic `account_sid:auth_token`).

Mocke `requests.Session.request` : chemins sous le compte, corps formulaire (noms
répétés), refus locaux (E.164, SID, bornes), pagination bornée au compte, signature
des webhooks, et TwiML échappé.
"""
from __future__ import annotations

import base64

import pytest

from oto.tools.common.credentials import MissingCredential
from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.twilio import (TwilioClient, bridge_twiml, expected_signature, forward_twiml,
                              valid_signature, voicemail_twiml)
from oto.tools.twilio import client as tc

SID = "AC" + "0" * 32
ROOT = f"https://api.twilio.com/2010-04-01/Accounts/{SID}"
CA = "CA" + "1" * 32
PN = "PN" + "2" * 32
RE_ = "RE" + "3" * 32


class _Resp:
    def __init__(self, status_code=200, body=None, content=b"{}"):
        self.status_code = status_code
        self._body = {} if body is None else body
        self.content = content
        self.text = str(self._body)
        self.headers = {}

    def json(self):
        return self._body


@pytest.fixture()
def capture(monkeypatch):
    seen = {"responses": []}

    def fake_request(self, method, url, **kwargs):
        seen.update(method=method, url=url, params=kwargs.get("params"), data=kwargs.get("data"),
                    headers=dict(self.headers))
        return seen["responses"].pop(0) if seen["responses"] else _Resp()

    monkeypatch.setattr(tc.requests.Session, "request", fake_request)
    return seen


@pytest.fixture()
def cli():
    return TwilioClient(account_sid=SID, auth_token="TOKEN")


def test_les_deux_champs_sont_exiges_et_le_sid_verifie():
    with pytest.raises(MissingCredential):
        TwilioClient(account_sid=SID, auth_token=None)
    with pytest.raises(MissingCredential):
        TwilioClient(account_sid=None, auth_token="t")
    with pytest.raises(ValueError):
        TwilioClient(account_sid="AC123", auth_token="t")


def test_le_jeton_du_compte_ne_sort_jamais(cli, capture):
    capture["responses"].append(_Resp(body={"sid": SID, "status": "active", "auth_token": "SECRET"}))
    assert "auth_token" not in cli.get_account()


def test_basic_auth_en_entete(cli, capture):
    cli.get_account()
    assert capture["url"] == ROOT + ".json"
    attendu = base64.b64encode(f"{SID}:TOKEN".encode()).decode()
    assert capture["headers"]["Authorization"] == f"Basic {attendu}"
    assert "TOKEN" not in capture["url"]


def test_recherche_de_numeros(cli, capture):
    cli.search_available_numbers("fr", "mobile", sms_enabled=True, page_size=5)
    assert capture["url"] == ROOT + "/AvailablePhoneNumbers/FR/Mobile.json"
    assert capture["params"] == {"SmsEnabled": "true", "PageSize": 5}
    with pytest.raises(ValueError):
        cli.search_available_numbers("France")
    with pytest.raises(ValueError):
        cli.search_available_numbers("FR", "premium")


def test_achat_corps_formulaire(cli, capture):
    cli.buy_number("+33939030001", bundle_sid="BU" + "4" * 32, sms_url="https://h/sms",
                   voice_url="https://h/voice")
    assert capture["method"] == "POST" and capture["url"] == ROOT + "/IncomingPhoneNumbers.json"
    assert ("PhoneNumber", "+33939030001") in capture["data"]
    assert ("BundleSid", "BU" + "4" * 32) in capture["data"]
    assert ("SmsUrl", "https://h/sms") in capture["data"]
    with pytest.raises(ValueError):
        cli.buy_number("0612345678")


def test_mise_a_jour_exige_un_champ_et_vide_une_url(cli, capture):
    with pytest.raises(ValueError):
        cli.update_number(PN)
    cli.update_number(PN, sms_url="")
    assert capture["data"] == [("SmsUrl", "")]


def test_liberation(cli, capture):
    capture["responses"].append(_Resp(204, content=b""))
    assert cli.release_number(PN) == {"deleted": True}
    assert capture["method"] == "DELETE" and capture["url"] == ROOT + f"/IncomingPhoneNumbers/{PN}.json"


def test_appel_un_seul_document_et_evenements_repetes(cli, capture):
    with pytest.raises(ValueError):
        cli.create_call("+33600000001", "+33939030001")
    cli.create_call("+33600000001", "+33939030001", twiml="<Response/>",
                    status_callback="https://h/s", status_callback_events=["answered", "completed"])
    data = capture["data"]
    assert [v for k, v in data if k == "StatusCallbackEvent"] == ["answered", "completed"]
    assert ("Twiml", "<Response/>") in data and ("To", "+33600000001") in data
    with pytest.raises(ValueError):
        cli.create_call("+33600000001", "+33939030001", url="https://h", status_callback_events=["x"])


def test_raccrocher_choisit_le_statut(cli, capture):
    capture["responses"] += [_Resp(body={"status": "ringing"}), _Resp()]
    cli.hangup_call(CA)
    assert capture["data"] == [("Status", "canceled")]
    capture["responses"] += [_Resp(body={"status": "in-progress"}), _Resp()]
    cli.hangup_call(CA)
    assert capture["data"] == [("Status", "completed")]


def test_media_d_un_enregistrement_en_octets(cli, capture):
    capture["responses"].append(_Resp(content=b"ID3audio"))
    assert cli.recording_media(RE_) == b"ID3audio"
    assert capture["url"] == ROOT + f"/Recordings/{RE_}.mp3"
    with pytest.raises(ValueError):
        cli.recording_media(RE_, "ogg")


def test_sms_expediteur_et_bornes(cli, capture):
    cli.send_message("+33600000001", body="Salut !", from_="MYSHOP")
    assert ("From", "MYSHOP") in capture["data"] and ("Body", "Salut !") in capture["data"]
    cli.send_message("+33600000001", media_urls=["https://h/a.png", "https://h/b.png"],
                     from_="+33939030001")
    assert [v for k, v in capture["data"] if k == "MediaUrl"] == ["https://h/a.png", "https://h/b.png"]
    with pytest.raises(ValueError):
        cli.send_message("+33600000001", body="x")
    with pytest.raises(ValueError):
        cli.send_message("+33600000001", body="x", from_="+3393", messaging_service_sid="MG" + "5" * 32)
    with pytest.raises(ValueError):
        cli.send_message("+33600000001", body="x", from_="TROP-LONG-NOM")
    with pytest.raises(ValueError):
        cli.send_message("+33600000001", body="x" * 1601, from_="MYSHOP")


def test_liste_de_messages_et_filtres(cli, capture):
    cli.list_messages(from_="+33600000001", date_sent_after="2026-11-01", page_size=20)
    assert capture["params"] == {"From": "+33600000001", "DateSent>": "2026-11-01", "PageSize": 20}
    with pytest.raises(ValueError):
        cli.list_messages(page_size=0)


def test_page_suivante_bornee_au_compte(cli, capture):
    cli.next_page(f"/2010-04-01/Accounts/{SID}/Messages.json?Page=1&PageToken=PA")
    assert capture["url"].startswith(ROOT + "/Messages.json")
    for mauvaise in ("https://evil/x", "/2010-04-01/Accounts/AC" + "9" * 32 + "/Calls.json",
                     f"/2010-04-01/Accounts/{SID}/../x"):
        with pytest.raises(ValueError):
            cli.next_page(mauvaise)


def test_erreur_amont(cli, capture):
    capture["responses"].append(_Resp(400, body={"code": 21211, "message": "Invalid 'To'"}))
    with pytest.raises(UpstreamHTTPError) as e:
        cli.get_call(CA)
    assert e.value.status_code == 400


def test_signature_exemple_de_la_documentation():
    # Exemple publié par Twilio (webhooks-security) : jeton, URL, paramètres et signature.
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234",
              "From": "+12349013030", "To": "+18005551212"}
    sig = expected_signature("12345", url, params)
    assert sig == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="
    assert valid_signature("12345", url, params, sig)
    assert not valid_signature("12345", url, dict(params, Digits="9"), sig)
    assert not valid_signature("", url, params, sig)
    assert not valid_signature("12345", url, params, "")


def test_twiml_echappe():
    doc = bridge_twiml('+336"><Hangup/>', caller_id="+33939030001", announce="<b>Bonjour</b>")
    assert "<Hangup/>" not in doc and "&lt;b&gt;" in doc
    assert "<Dial" in forward_twiml("+33600000001", action="https://h/a")
    assert 'maxLength="60"' in voicemail_twiml(max_length=60)
    with pytest.raises(ValueError):
        bridge_twiml("+33600000001", caller_id="+33939030001", record="always")


def test_types_de_numeros_vendus(cli, capture):
    capture["responses"].append(_Resp(body={"country_code": "GB", "subresource_uris": {
        "local": "/x", "mobile": "/y", "toll_free": "/z"}}))
    assert cli.available_number_types("gb") == ["local", "mobile", "toll_free"]
    assert capture["url"] == ROOT + "/AvailablePhoneNumbers/GB.json"

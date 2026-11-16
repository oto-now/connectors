"""La description demodesk, jouée par le moteur (`oto.connectors.engine`) contre un faux tiers, sans réseau.

Demodesk n'a pas de client Python : la description est la seule source des requêtes. Ici, ce qu'elle envoie (chemins,
filtres Ransack en objet profond `filter[...]`, listes en crochets, corps du lot de transcriptions, jeton porteur), ce
qu'elle retire des réponses (le lien signé de la vidéo, la copie HTML d'un résumé), ses pages par curseur
(`meta.nextCursor` tant que `meta.hasNext`), sa table d'erreurs, et les deux succès sans transcription (202, 204).
"""
from __future__ import annotations

import pytest

import engine_fake
from engine_fake import answer, play, sent
from oto.connectors import descriptions, engine
from oto.connectors.engine import transport

BASE = "https://demodesk.com/api/v2"
KEY = "test-key-not-real-0123456789"
TOKEN = "d0584dc4e31be4a0"


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(transport, "_sleep", lambda seconds: None)


@pytest.fixture(scope="module")
def demodesk() -> dict:
    return descriptions.load("demodesk")


def _one(description: dict, function: str, arguments: dict, body=None):
    result, vendor = play(description, function, arguments, answer(200, body if body is not None else {"data": []}),
                          credential={"api_key": KEY})
    (request,) = vendor.requests
    return result, sent(request, BASE)


def test_every_function_reads(demodesk):
    assert {f["name"]: f["class"] for f in demodesk["functions"]} == {
        "get_current_user": "read", "list_users": "read", "list_meetings": "read", "list_recordings": "read",
        "get_recording": "read", "get_transcript": "read", "get_transcripts": "read", "list_summaries": "read",
        "list_scorecards": "read"}
    assert demodesk["connector"]["modes"] == ["byo_user", "byo_org"]
    assert demodesk["connector"]["probe"] == {"function": "get_current_user"}


def test_the_key_goes_as_a_bearer_token(demodesk):
    _, parts = _one(demodesk, "get_current_user", {}, {"data": {"id": "1"}})
    assert (parts["method"], parts["path"], parts["query"]) == ("GET", "/me", [])
    assert parts["headers"]["authorization"] == f"Bearer {KEY}"
    assert "api-key" not in parts["headers"]


def test_recording_filters_are_ransack_keys_with_brackets(demodesk):
    _, parts = _one(demodesk, "list_recordings", {
        "meeting_from": "2026-11-01T00:00:00Z", "meeting_before": "2026-12-01T00:00:00Z",
        "created_from": "2026-10-01T00:00:00Z", "created_before": "2026-11-15T00:00:00Z",
        "hosts": ["me", "host@example.test"], "attendees": ["buyer@example.test"],
        "attendee_domains": ["example.test", "example.de"], "meeting_id": "123", "group_ids": ["99"],
        "statuses": ["ready"], "postprocessing_statuses": ["done"], "access": ["company_wide"],
        "cursor": "c-2", "limit": 50})
    assert (parts["method"], parts["path"]) == ("GET", "/recordings")
    assert parts["query"] == [
        ["filter[demo_start_date_gteq]", "2026-11-01T00:00:00Z"], ["filter[demo_start_date_lt]", "2026-12-01T00:00:00Z"],
        ["filter[created_at_gteq]", "2026-10-01T00:00:00Z"], ["filter[created_at_lt]", "2026-11-15T00:00:00Z"],
        ["filter[host_in][]", "me"], ["filter[host_in][]", "host@example.test"],
        ["filter[attendee_in][]", "buyer@example.test"],
        ["filter[attendee_domain_in][]", "example.test"], ["filter[attendee_domain_in][]", "example.de"],
        ["filter[demo_id_eq]", "123"], ["filter[group_id_in][]", "99"], ["filter[status_in][]", "ready"],
        ["filter[postprocessing_status_in][]", "done"], ["filter[access_in][]", "company_wide"],
        ["cursor", "c-2"], ["limit", "50"]]


def test_an_absent_filter_is_not_sent(demodesk):
    _, parts = _one(demodesk, "list_recordings", {})
    assert parts["query"] == []


def test_meeting_filters(demodesk):
    _, parts = _one(demodesk, "list_meetings", {
        "starts_from": "2026-10-01T00:00:00Z", "starts_before": "2026-11-01T00:00:00Z", "hosts": ["me"],
        "statuses": ["ended", "canceled"]})
    assert (parts["method"], parts["path"]) == ("GET", "/meetings")
    assert parts["query"] == [
        ["filter[start_date_gteq]", "2026-10-01T00:00:00Z"], ["filter[start_date_lt]", "2026-11-01T00:00:00Z"],
        ["filter[host_in][]", "me"], ["filter[status_in][]", "ended"], ["filter[status_in][]", "canceled"]]


@pytest.mark.parametrize("function, path", [
    ("get_recording", f"/recordings/{TOKEN}"),
    ("get_transcript", f"/recordings/{TOKEN}/transcript"),
    ("list_summaries", f"/recordings/{TOKEN}/summaries"),
    ("list_scorecards", f"/recordings/{TOKEN}/scorecards"),
])
def test_one_recording_routes(demodesk, function, path):
    _, parts = _one(demodesk, function, {"token": TOKEN}, {"data": {}})
    assert (parts["method"], parts["path"], parts["query"], parts["body"]) == ("GET", path, [], None)


def test_a_translated_transcript_asks_its_language(demodesk):
    _, parts = _one(demodesk, "get_transcript", {"token": TOKEN, "lang": "fr"}, {"data": {}})
    assert parts["query"] == [["lang", "fr"]]


def test_the_transcript_batch_is_a_json_body(demodesk):
    _, parts = _one(demodesk, "get_transcripts", {"tokens": [TOKEN, "a1b2c3d4e5f60718"], "format": "plaintext",
                                                  "lang": "de"}, {"data": [], "meta": {"requestedCount": 2}})
    assert (parts["method"], parts["path"], parts["query"]) == ("POST", "/transcripts/batch", [])
    assert parts["body"] == {"recordingTokens": [TOKEN, "a1b2c3d4e5f60718"], "format": "plaintext", "lang": "de"}


def test_the_signed_video_link_never_comes_back(demodesk):
    signed = "https://media.example.test/video.mp4?token=t"
    page = {"data": [{"recordingToken": TOKEN, "name": "Call", "temporaryDirectUrl": signed}],
            "meta": {"hasNext": False, "limit": 100, "nextCursor": None}}
    listed, _ = _one(demodesk, "list_recordings", {}, page)
    one, _ = _one(demodesk, "get_recording", {"token": TOKEN},
                  {"data": {"recordingToken": TOKEN, "temporaryDirectUrl": signed}})
    assert signed not in repr(listed) and signed not in repr(one)
    assert listed["items"] == [{"recordingToken": TOKEN, "name": "Call"}]
    assert one == {"data": {"recordingToken": TOKEN}}


def test_a_summary_comes_without_its_html_copy(demodesk):
    result, _ = _one(demodesk, "list_summaries", {"token": TOKEN},
                     {"data": [{"summaryId": "1", "content": "Recap", "htmlContent": "<p>Recap</p>"}]})
    assert result == {"data": [{"summaryId": "1", "content": "Recap"}]}


def test_lists_page_by_the_next_cursor(demodesk):
    first = {"data": [{"demoId": "1"}], "meta": {"hasNext": True, "limit": 1, "nextCursor": "n2"}}
    last = {"data": [{"demoId": "2"}], "meta": {"hasNext": False, "limit": 1, "nextCursor": None}}
    result, vendor = play(demodesk, "list_meetings", {"limit": 1, "all_pages": True}, answer(200, first),
                          answer(200, last), credential={"api_key": KEY})
    one, two = (sent(r, BASE) for r in vendor.requests)
    assert one["query"] == [["limit", "1"]]
    assert two["query"] == [["cursor", "n2"], ["limit", "1"]]
    assert (result["items"], result["pages"], result["next_cursor"]) == ([{"demoId": "1"}, {"demoId": "2"}], 2, None)


def test_a_refused_key_marks_the_account(demodesk):
    with pytest.raises(engine.Refused) as raised:
        play(demodesk, "get_current_user", {}, answer(401, {"error": {"code": "unauthorized", "message": "Bad key"}}),
             credential={"api_key": KEY})
    assert (raised.value.code, raised.value.marks_account) == ("key_refused", "credential_rejected")


@pytest.mark.parametrize("status, code, retryable", [
    (400, "invalid_request", False), (403, "forbidden", False), (404, "not_found", False),
    (503, "upstream_unavailable", True)])
def test_refusals_follow_the_table(demodesk, status, code, retryable):
    with pytest.raises(engine.Refused) as raised:
        play(demodesk, "get_recording", {"token": TOKEN}, answer(status, {"error": {"message": "x"}}),
             credential={"api_key": KEY})
    assert (raised.value.code, raised.value.retryable, raised.value.marks_account) == (code, retryable, None)


def test_a_transcript_still_processing_comes_back_as_its_answer(demodesk):
    body = {"error": {"code": "processing", "message": "Transcript is still being processed."}}
    result, _ = play(demodesk, "get_transcript", {"token": TOKEN}, answer(202, body), credential={"api_key": KEY})
    assert result == body


def test_a_rate_limit_is_retried_then_refused(demodesk):
    limited = answer(429, {"error": {"message": "slow down"}}, headers={"Retry-After": "1"})
    with pytest.raises(engine.Refused) as raised:
        play(demodesk, "get_recording", {"token": TOKEN}, limited, limited, limited, credential={"api_key": KEY})
    assert (raised.value.code, raised.value.retryable) == ("rate_limited", True)


def test_an_empty_transcript_is_an_empty_answer(demodesk):
    result, _ = play(demodesk, "get_transcript", {"token": TOKEN}, answer(204), credential={"api_key": KEY})
    assert result == {}

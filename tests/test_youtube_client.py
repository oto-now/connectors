"""YouTube client: request shapes (offline, fake session), the read-merge-write of
`update_video`, and typed refusals."""
import json

import pytest

from oto.tools.common.credentials import MissingCredential
from oto.tools.google import youtube as yt


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = b"" if status == 204 else json.dumps(self._body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        return self.answers.pop(0) if self.answers else _Resp()


def _client(*answers):
    s = _Session(*answers)
    return yt.YouTubeClient("tok", session=s), s


VIDEO = {
    "id": "v1",
    "snippet": {"title": "Old", "description": "Keep me", "tags": ["a"], "categoryId": "28",
                "defaultLanguage": "en", "channelId": "UC1", "thumbnails": {"x": 1},
                "localized": {"title": "Old"}},
    "status": {"privacyStatus": "public", "embeddable": True, "license": "youtube",
               "publicStatsViewable": True, "uploadStatus": "processed"},
}


def test_token_required():
    with pytest.raises(MissingCredential):
        yt.YouTubeClient("")


def test_scopes():
    assert yt.SCOPES == [
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]


def test_bearer_and_no_none_params():
    c, s = _client(_Resp(body={"items": [{"id": "UC1"}]}))
    assert c.my_channels() == [{"id": "UC1"}]
    call = s.calls[0]
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["url"] == "https://www.googleapis.com/youtube/v3/channels"
    assert call["params"]["mine"] == "true"
    assert "tok" not in json.dumps(call["params"])


def test_list_uploads_reads_the_uploads_playlist_not_search():
    c, s = _client(
        _Resp(body={"items": [{"id": "UC1", "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}),
        _Resp(body={"items": [{"contentDetails": {"videoId": "v1"}}], "nextPageToken": "n"}),
    )
    out = c.list_uploads(max_results=10)
    assert out["playlist_id"] == "UU1" and out["next_page_token"] == "n"
    assert s.calls[1]["url"].endswith("/playlistItems")
    assert s.calls[1]["params"]["playlistId"] == "UU1"
    assert not any(c_["url"].endswith("/search") for c_ in s.calls)


def test_list_uploads_without_channel():
    c, _ = _client(_Resp(body={"items": []}))
    with pytest.raises(ValueError, match="no YouTube channel"):
        c.list_uploads()


def test_get_videos_caps_ids():
    c, _ = _client()
    with pytest.raises(ValueError):
        c.get_videos([])
    with pytest.raises(ValueError, match="at most 50"):
        c.get_videos([str(i) for i in range(51)])


def test_comment_threads_needs_one_target():
    c, s = _client()
    with pytest.raises(ValueError):
        c.list_comment_threads()
    with pytest.raises(ValueError):
        c.list_comment_threads(video_id="v", channel_id="c")
    c.list_comment_threads(channel_id="UC1", moderation_status="heldForReview")
    p = s.calls[0]["params"]
    assert p["allThreadsRelatedToChannelId"] == "UC1" and "videoId" not in p
    assert p["moderationStatus"] == "heldForReview"


def test_report_is_on_my_channel():
    c, s = _client()
    c.report(start_date="2026-10-01", end_date="2026-10-31", metrics="views", dimensions="day")
    call = s.calls[0]
    assert call["url"] == "https://youtubeanalytics.googleapis.com/v2/reports"
    assert call["params"]["ids"] == "channel==MINE"
    assert "filters" not in call["params"]


def test_plan_keeps_untouched_fields_and_drops_read_only():
    body, diff = yt.plan_video_update(VIDEO, {"title": "New"})
    assert diff == {"title": {"from": "Old", "to": "New"}}
    assert set(body) == {"id", "snippet"}
    assert body["snippet"] == {"title": "New", "description": "Keep me", "tags": ["a"],
                               "categoryId": "28", "defaultLanguage": "en"}
    assert VIDEO["snippet"]["title"] == "Old"  # not mutated


def test_plan_status_part_keeps_its_fields():
    body, diff = yt.plan_video_update(VIDEO, {"privacy_status": "unlisted"})
    assert set(body) == {"id", "status"}
    assert body["status"] == {"privacyStatus": "unlisted", "embeddable": True,
                              "license": "youtube", "publicStatsViewable": True}


def test_plan_refusals():
    with pytest.raises(ValueError, match="nothing to change"):
        yt.plan_video_update(VIDEO, {"title": None})
    with pytest.raises(ValueError, match="unknown"):
        yt.plan_video_update(VIDEO, {"thumbnail": "x"})
    with pytest.raises(ValueError, match="privacy_status"):
        yt.plan_video_update(VIDEO, {"privacy_status": "secret"})
    with pytest.raises(ValueError, match="publish_at"):
        yt.plan_video_update(VIDEO, {"publish_at": "2026-12-01T10:00:00Z"})
    with pytest.raises(ValueError, match="empty"):
        yt.plan_video_update(VIDEO, {"title": "  "})


def test_plan_schedule_on_private():
    body, _ = yt.plan_video_update(VIDEO, {"privacy_status": "private",
                                           "publish_at": "2026-12-01T10:00:00Z"})
    assert body["status"]["publishAt"] == "2026-12-01T10:00:00Z"


def test_update_video_reads_then_puts_touched_part():
    c, s = _client(_Resp(body={"items": [VIDEO]}), _Resp(body={"id": "v1"}))
    out = c.update_video("v1", {"description": "New desc"})
    assert out["changes"] == {"description": {"from": "Keep me", "to": "New desc"}}
    put = s.calls[1]
    assert put["method"] == "PUT" and put["params"] == {"part": "snippet"}
    assert put["json"]["snippet"]["title"] == "Old"
    assert put["json"]["snippet"]["categoryId"] == "28"


def test_update_video_dry_run_and_noop_do_not_write():
    c, s = _client(_Resp(body={"items": [VIDEO]}))
    out = c.update_video("v1", {"title": "New"}, dry_run=True)
    assert out["dry_run"] is True and "video" not in out and len(s.calls) == 1
    c, s = _client(_Resp(body={"items": [VIDEO]}))
    out = c.update_video("v1", {"title": "Old"})
    assert out["changes"] == {} and len(s.calls) == 1


def test_update_video_not_found():
    c, _ = _client(_Resp(body={"items": []}))
    with pytest.raises(ValueError, match="not found"):
        c.update_video("nope", {"title": "x"})


def test_set_thumbnail_binary_upload():
    c, s = _client(_Resp(body={"items": [{"default": {}}]}))
    c.set_thumbnail("v1", b"\x89PNG", "image/png")
    call = s.calls[0]
    assert call["url"] == "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
    assert call["params"] == {"videoId": "v1", "uploadType": "media"}
    assert call["data"] == b"\x89PNG" and call["headers"]["Content-Type"] == "image/png"
    with pytest.raises(ValueError, match="JPEG or PNG"):
        c.set_thumbnail("v1", b"x", "image/gif")
    with pytest.raises(ValueError, match="empty"):
        c.set_thumbnail("v1", b"", "image/png")


def test_moderation_rules_and_204():
    c, s = _client(_Resp(status=204))
    assert c.set_moderation_status(["c1", "c2"], "rejected", ban_author=True) == {}
    assert s.calls[0]["params"] == {"id": "c1,c2", "moderationStatus": "rejected",
                                    "banAuthor": "true"}
    with pytest.raises(ValueError, match="ban_author"):
        c.set_moderation_status(["c1"], "published", ban_author=True)
    with pytest.raises(ValueError):
        c.set_moderation_status(["c1"], "deleted")


def test_reply_and_playlist_bodies():
    c, s = _client(_Resp(body={"id": "r"}), _Resp(body={"id": "pi"}))
    c.reply_to_comment("c1", "Thanks!")
    assert s.calls[0]["json"] == {"snippet": {"parentId": "c1", "textOriginal": "Thanks!"}}
    c.add_to_playlist("PL1", "v1", position=0)
    assert s.calls[1]["json"]["snippet"] == {
        "playlistId": "PL1", "resourceId": {"kind": "youtube#video", "videoId": "v1"},
        "position": 0}
    with pytest.raises(ValueError):
        c.reply_to_comment("c1", " ")


def test_error_carries_reasons():
    body = {"error": {"code": 403, "message": "Access Not Configured", "status": "PERMISSION_DENIED",
                      "errors": [{"reason": "accessNotConfigured"}],
                      "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                   "reason": "SERVICE_DISABLED"}]}}
    c, _ = _client(_Resp(status=403, body=body))
    with pytest.raises(yt.YouTubeError) as e:
        c.my_channels()
    err = e.value
    assert err.status_code == 403
    assert err.has("SERVICE_DISABLED") and err.has("accessNotConfigured")
    assert err.message == "Access Not Configured"


# --- upload_video (resumable) -------------------------------------------------------

class _UpResp(_Resp):
    def __init__(self, status=200, body=None, headers=None):
        super().__init__(status, body)
        self.headers = headers or {}


SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=u1"
CHUNK = 256 * 1024


def test_upload_video_resumable_en_morceaux():
    data = b"v" * (CHUNK * 2 + 10)
    c, s = _client(
        _UpResp(200, {}, {"Location": SESSION}),
        _UpResp(308, {}, {"Range": f"bytes=0-{CHUNK - 1}"}),
        _UpResp(308, {}, {"Range": f"bytes=0-{2 * CHUNK - 1}"}),
        _UpResp(201, {"id": "new1", "status": {"privacyStatus": "private"}}),
    )
    out = c.upload_video(data, "video/mp4", title="Demo", tags=["x"],
                         notify_subscribers=False, chunk_size=CHUNK)
    assert out["id"] == "new1"
    init = s.calls[0]
    assert init["method"] == "POST" and init["url"].endswith("/upload/youtube/v3/videos")
    assert init["params"] == {"uploadType": "resumable", "part": "snippet,status",
                              "notifySubscribers": "false"}
    assert init["headers"]["X-Upload-Content-Length"] == str(len(data))
    assert init["headers"]["X-Upload-Content-Type"] == "video/mp4"
    assert init["json"] == {"snippet": {"title": "Demo", "categoryId": "22", "tags": ["x"]},
                            "status": {"privacyStatus": "private",
                                       "selfDeclaredMadeForKids": False}}
    ranges = [k["headers"]["Content-Range"] for k in s.calls[1:]]
    assert ranges == [f"bytes 0-{CHUNK - 1}/{len(data)}",
                      f"bytes {CHUNK}-{2 * CHUNK - 1}/{len(data)}",
                      f"bytes {2 * CHUNK}-{len(data) - 1}/{len(data)}"]
    assert all(k["url"] == SESSION and k["allow_redirects"] is False for k in s.calls[1:])
    assert b"".join(k["data"] for k in s.calls[1:]) == data


def test_upload_video_reprend_apres_un_5xx_la_ou_google_s_est_arrete():
    data = b"v" * (CHUNK * 2)
    c, s = _client(
        _UpResp(200, {}, {"Location": SESSION}),
        _UpResp(503, {"error": {"code": 503, "message": "busy"}}),
        _UpResp(308, {}, {"Range": f"bytes=0-{CHUNK - 1}"}),   # probe: holds chunk 1
        _UpResp(201, {"id": "new1"}),
    )
    assert c.upload_video(data, "video/mp4", title="T", chunk_size=CHUNK)["id"] == "new1"
    assert s.calls[2]["headers"]["Content-Range"] == f"bytes */{len(data)}"
    assert s.calls[3]["headers"]["Content-Range"] == f"bytes {CHUNK}-{2 * CHUNK - 1}/{len(data)}"


def test_upload_video_refus_google_type():
    c, _ = _client(_UpResp(400, {"error": {"code": 400, "message": "x",
                                           "errors": [{"reason": "uploadLimitExceeded"}]}}))
    with pytest.raises(yt.YouTubeError) as e:
        c.upload_video(b"v", "video/mp4", title="T")
    assert e.value.has("uploadLimitExceeded")


@pytest.mark.parametrize("kw,msg", [
    (dict(video=b"", mime_type="video/mp4", title="T"), "empty"),
    (dict(video=b"v", mime_type="image/png", title="T"), "video/"),
    (dict(video=b"v", mime_type="video/mp4", title=" "), "title"),
    (dict(video=b"v", mime_type="video/mp4", title="T", privacy_status="public",
          publish_at="2026-12-01T09:00:00Z"), "publish_at"),
    (dict(video=b"v", mime_type="video/mp4", title="T", chunk_size=1000), "256 KB"),
])
def test_upload_video_refuse_avant_tout_appel(kw, msg):
    c, s = _client()
    with pytest.raises(ValueError, match=msg):
        c.upload_video(**kw)
    assert s.calls == []

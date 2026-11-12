"""YouTube client (REST) — a channel's Studio surface, with an OAuth2 user access token.

Two Google APIs, one token:

- **YouTube Data API v3** (`www.googleapis.com/youtube/v3`): the channel, its uploads,
  playlists, comments; writes on video metadata, thumbnails, comments, playlists;
- **YouTube Analytics API v2** (`youtubeanalytics.googleapis.com/v2`): `reports.query`
  on `channel==MINE`.

**The channel is the one chosen at consent.** A Google account can own several channels
(brand accounts): Google asks which one the token is for, and `mine=true` answers that
one. `my_channels()` says which.

**`videos.update` replaces a part wholesale**: a property the request leaves out is
DELETED (description, tags, privacy…), and `snippet.title` + `snippet.categoryId` are
required whenever `snippet` is sent. `update_video` therefore reads the video, merges
the requested changes into the writable fields, and writes back only the parts it
touched. `plan_video_update` is the pure merge, for a dry run.

**Quota** is per Google Cloud project (the OAuth app), 10,000 units a day: a list costs
1, a write ~50, `search.list` has its own 100 calls a day. Own uploads are read through
the channel's uploads playlist (1 unit a page), never `search.list`.

**Uploads** (`videos.insert`) have their own bucket: 100 a day per Cloud project, 1 unit
each. `upload_video` uses the resumable protocol (session, then chunks of 256 KB
multiples, a 308 between chunks, 201 at the end) and resumes from the byte Google says
it holds after a 5xx or a dropped connection.

Scopes: `youtube.force-ssl` (Data API, writes, comments and uploads included), `youtube.readonly`
and `yt-analytics.readonly` (Analytics reports). Requires: requests.
"""
from __future__ import annotations

import copy
from typing import Any, Iterable, Optional

import requests

from ...common import UpstreamHTTPError
from ...common.credentials import require

SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

_DATA = "https://www.googleapis.com/youtube/v3"
_UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
_ANALYTICS = "https://youtubeanalytics.googleapis.com/v2"
_HTTP_TIMEOUT = (10, 30)

#: `videos.list` / `channels.list` take at most 50 ids, a page at most 50 items.
MAX_IDS = 50
MAX_PAGE = 50
#: `thumbnails.set` limits (method reference): JPEG or PNG, 50 MB.
THUMBNAIL_MIME_TYPES = ("image/jpeg", "image/png")
THUMBNAIL_MAX_BYTES = 50 * 1024 * 1024
#: `videos.insert` (method reference): `video/*` or `application/octet-stream`, 256 GB.
#: Resumable protocol: every chunk but the last is a multiple of 256 KB.
VIDEO_MAX_BYTES = 256 * 1024 ** 3
UPLOAD_CHUNK = 8 * 1024 * 1024
_UPLOAD_RETRIES = 3
MODERATION_STATUSES = ("heldForReview", "published", "rejected")
PRIVACY_STATUSES = ("private", "unlisted", "public")
LICENSES = ("youtube", "creativeCommon")

#: Writable fields of `update_video`, keyed by argument → (part, Google property).
VIDEO_FIELDS: dict[str, tuple[str, str]] = {
    "title": ("snippet", "title"),
    "description": ("snippet", "description"),
    "tags": ("snippet", "tags"),
    "category_id": ("snippet", "categoryId"),
    "default_language": ("snippet", "defaultLanguage"),
    "privacy_status": ("status", "privacyStatus"),
    "publish_at": ("status", "publishAt"),
    "embeddable": ("status", "embeddable"),
    "license": ("status", "license"),
    "public_stats_viewable": ("status", "publicStatsViewable"),
    "made_for_kids": ("status", "selfDeclaredMadeForKids"),
    "contains_synthetic_media": ("status", "containsSyntheticMedia"),
}
#: What a part keeps from the read resource when written back (the rest is read-only).
_WRITABLE = {
    "snippet": ("title", "description", "tags", "categoryId", "defaultLanguage"),
    "status": ("privacyStatus", "publishAt", "embeddable", "license",
               "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia"),
}


class YouTubeError(UpstreamHTTPError):
    """Refusal from a YouTube API.

    `reasons` = every reason Google gives — `errors[].reason` (`forbidden`,
    `quotaExceeded`, `videoNotFound`, `invalidImage`, `uploadRateLimitExceeded`…) and
    `details[].reason` (`SERVICE_DISABLED`, `ACCESS_TOKEN_SCOPE_INSUFFICIENT`…) —,
    `message` = Google's text. Branch on the reasons, never on the text."""

    def __init__(self, status_code: int, body: Any, *, method: Optional[str] = None,
                 url: Optional[str] = None):
        err = (body.get("error") if isinstance(body, dict) else None) or {}
        self.reasons: list[str] = []
        for e in err.get("errors") or []:
            if e.get("reason"):
                self.reasons.append(str(e["reason"]))
        for d in err.get("details") or []:
            if isinstance(d, dict) and d.get("reason"):
                self.reasons.append(str(d["reason"]))
        if err.get("status"):
            self.reasons.append(str(err["status"]))
        text = body if isinstance(body, str) else ""
        self.message: str = (err.get("message") or text[:300]).strip()
        super().__init__(status_code, self.message, service="youtube", method=method, url=url)

    def has(self, *reasons: str) -> bool:
        return any(r in self.reasons for r in reasons)


def _ids(values: Iterable[str], what: str) -> str:
    ids = [str(v).strip() for v in (values or []) if str(v).strip()]
    if not ids:
        raise ValueError(f"at least one {what} is required")
    if len(ids) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} {what}s per call, got {len(ids)}")
    return ",".join(ids)


def _page(max_results: int) -> int:
    if not 1 <= int(max_results) <= MAX_PAGE:
        raise ValueError(f"max_results must be between 1 and {MAX_PAGE}")
    return int(max_results)


def plan_video_update(video: dict, changes: dict) -> tuple[dict, dict]:
    """Merge `changes` (keys of `VIDEO_FIELDS`, None = not changed) into a video read
    with `snippet,status`. → `(body, diff)`: the `videos.update` body (only the parts a
    change touches, each with all its writable fields kept), and
    `{argument: {"from": current, "to": new}}` for the values that really change.

    Unknown argument, bad enum, or a part missing from `video` → `ValueError`."""
    unknown = set(changes) - set(VIDEO_FIELDS)
    if unknown:
        raise ValueError(f"unknown video field(s): {', '.join(sorted(unknown))}")
    wanted = {k: v for k, v in changes.items() if v is not None}
    if not wanted:
        raise ValueError("nothing to change: pass at least one field")
    if "privacy_status" in wanted and wanted["privacy_status"] not in PRIVACY_STATUSES:
        raise ValueError(f"privacy_status must be one of {', '.join(PRIVACY_STATUSES)}")
    if "license" in wanted and wanted["license"] not in LICENSES:
        raise ValueError(f"license must be one of {', '.join(LICENSES)}")
    if "tags" in wanted and not isinstance(wanted["tags"], list):
        raise ValueError("tags must be a list of strings")
    if "title" in wanted and not str(wanted["title"]).strip():
        raise ValueError("title cannot be empty")

    body: dict = {"id": video.get("id")}
    diff: dict = {}
    for arg, value in wanted.items():
        part, prop = VIDEO_FIELDS[arg]
        if part not in video:
            raise ValueError(f"the video was read without its `{part}` part")
        if part not in body:
            current = video.get(part) or {}
            body[part] = {k: copy.deepcopy(current[k]) for k in _WRITABLE[part] if k in current}
        before = (video.get(part) or {}).get(prop)
        if before != value:
            diff[arg] = {"from": before, "to": value}
        body[part][prop] = value
    if "status" in body and body["status"].get("privacyStatus") != "private":
        # Google refuses a publishAt on a non-private video; a scheduled video made
        # public/unlisted is published now, so its schedule goes.
        if "publish_at" in wanted:
            raise ValueError("publish_at needs privacy_status 'private' (a scheduled video)")
        body["status"].pop("publishAt", None)
    return body, diff


class YouTubeClient:
    """YouTube Data + Analytics client for ONE OAuth2 access token."""

    def __init__(self, access_token: str, *, session: Optional[requests.Session] = None,
                 timeout: tuple[float, float] = _HTTP_TIMEOUT):
        self._token = require(access_token, "YOUTUBE_ACCESS_TOKEN")
        self._session = session or requests.Session()
        self._timeout = timeout

    # --- transport ----------------------------------------------------------------

    def _call(self, method: str, url: str, *, params: Optional[dict] = None,
              json: Optional[dict] = None, data: Optional[bytes] = None,
              content_type: Optional[str] = None) -> Any:
        headers = {"Authorization": f"Bearer {self._token}"}
        if content_type:
            headers["Content-Type"] = content_type
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self._session.request(method, url, headers=headers, params=clean,
                                     json=json, data=data, timeout=self._timeout)
        if resp.status_code >= 400:
            try:
                payload: Any = resp.json()
            except ValueError:
                payload = resp.text
            raise YouTubeError(resp.status_code, payload, method=method, url=url)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # --- reads: channel, videos, playlists ------------------------------------------

    def my_channels(self, parts: str = "snippet,statistics,contentDetails,status") -> list[dict]:
        """The channel(s) of the token (`mine=true`) — usually one, the one chosen at
        consent. `contentDetails.relatedPlaylists.uploads` = its uploads playlist."""
        data = self._call("GET", f"{_DATA}/channels", params={"part": parts, "mine": "true"})
        return data.get("items") or []

    def list_playlist_items(self, playlist_id: str, *, max_results: int = 25,
                            page_token: Optional[str] = None) -> dict:
        """One page of a playlist → raw `{items, nextPageToken?, pageInfo}`."""
        return self._call("GET", f"{_DATA}/playlistItems", params={
            "part": "snippet,contentDetails,status", "playlistId": require(playlist_id, "playlist_id"),
            "maxResults": _page(max_results), "pageToken": page_token})

    def list_uploads(self, *, max_results: int = 25, page_token: Optional[str] = None) -> dict:
        """One page of the channel's uploads, newest first, private and scheduled ones
        included (the uploads playlist, not `search.list`). → `{channel_id,
        playlist_id, items, next_page_token}`; `items` are playlist items, whose
        `contentDetails.videoId` feeds `get_videos`."""
        channels = self.my_channels(parts="contentDetails")
        if not channels:
            raise ValueError("this Google account has no YouTube channel")
        ch = channels[0]
        uploads = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if not uploads:
            raise ValueError("the channel has no uploads playlist yet (no video uploaded)")
        page = self.list_playlist_items(uploads, max_results=max_results, page_token=page_token)
        return {"channel_id": ch.get("id"), "playlist_id": uploads,
                "items": page.get("items") or [], "next_page_token": page.get("nextPageToken")}

    def get_videos(self, video_ids: Iterable[str],
                   parts: str = "snippet,status,statistics,contentDetails") -> list[dict]:
        """Up to 50 videos by id → raw resources (unknown or hidden ids are just absent)."""
        data = self._call("GET", f"{_DATA}/videos", params={
            "part": parts, "id": _ids(video_ids, "video id"), "maxResults": MAX_IDS})
        return data.get("items") or []

    def list_playlists(self, *, max_results: int = 25, page_token: Optional[str] = None) -> dict:
        """One page of the channel's own playlists → raw `{items, nextPageToken?}`."""
        return self._call("GET", f"{_DATA}/playlists", params={
            "part": "snippet,status,contentDetails", "mine": "true",
            "maxResults": _page(max_results), "pageToken": page_token})

    def list_video_categories(self, region_code: str = "US") -> list[dict]:
        """Assignable video categories of a region (`categoryId` of `update_video`)."""
        data = self._call("GET", f"{_DATA}/videoCategories",
                          params={"part": "snippet", "regionCode": region_code})
        return [c for c in data.get("items") or []
                if (c.get("snippet") or {}).get("assignable")]

    # --- reads: comments -----------------------------------------------------------------

    def list_comment_threads(self, *, video_id: Optional[str] = None,
                             channel_id: Optional[str] = None,
                             moderation_status: Optional[str] = None,
                             search_terms: Optional[str] = None, order: str = "time",
                             max_results: int = 20, page_token: Optional[str] = None) -> dict:
        """One page of top-level comments with their first replies: on ONE video, or on
        every video of a channel (`channel_id`, = `allThreadsRelatedToChannelId`).
        `moderation_status` (`heldForReview`, `likelySpam`, `published`) reads the
        moderation queue — the channel owner's token only. → raw `{items, nextPageToken?}`."""
        if (video_id is None) == (channel_id is None):
            raise ValueError("pass exactly one of video_id or channel_id")
        if order not in ("time", "relevance"):
            raise ValueError("order must be 'time' or 'relevance'")
        if not 1 <= int(max_results) <= 100:
            raise ValueError("max_results must be between 1 and 100")
        return self._call("GET", f"{_DATA}/commentThreads", params={
            "part": "snippet,replies", "videoId": video_id,
            "allThreadsRelatedToChannelId": channel_id,
            "moderationStatus": moderation_status, "searchTerms": search_terms,
            "order": order, "textFormat": "plainText", "maxResults": int(max_results),
            "pageToken": page_token})

    def list_replies(self, parent_id: str, *, max_results: int = 50,
                     page_token: Optional[str] = None) -> dict:
        """One page of the replies to a top-level comment → raw `{items, nextPageToken?}`."""
        return self._call("GET", f"{_DATA}/comments", params={
            "part": "snippet", "parentId": require(parent_id, "parent_id"),
            "textFormat": "plainText", "maxResults": _page(max_results),
            "pageToken": page_token})

    # --- reads: analytics ------------------------------------------------------------------

    def report(self, *, start_date: str, end_date: str, metrics: str,
               dimensions: Optional[str] = None, filters: Optional[str] = None,
               sort: Optional[str] = None, max_results: Optional[int] = None,
               start_index: Optional[int] = None, currency: Optional[str] = None) -> dict:
        """YouTube Analytics `reports.query` on the token's channel (`channel==MINE`).
        Dates `YYYY-MM-DD`; metrics/dimensions comma-separated (`views,
        estimatedMinutesWatched`, `day`, `video`…); `filters` like `video==ID`.
        → raw `{columnHeaders: [{name, columnType, dataType}], rows: [[…]]}`."""
        return self._call("GET", f"{_ANALYTICS}/reports", params={
            "ids": "channel==MINE", "startDate": require(start_date, "start_date"),
            "endDate": require(end_date, "end_date"), "metrics": require(metrics, "metrics"),
            "dimensions": dimensions, "filters": filters, "sort": sort,
            "maxResults": max_results, "startIndex": start_index, "currency": currency})

    # --- writes --------------------------------------------------------------------------------

    def update_video(self, video_id: str, changes: dict, *, dry_run: bool = False) -> dict:
        """Change a video's metadata (keys of `VIDEO_FIELDS`), keeping everything else:
        read `snippet,status` → merge → `videos.update` on the touched parts only.
        → `{video_id, changes: {field: {from, to}}, dry_run, video?}` (`video` = Google's
        answer, absent on a dry run or when nothing really changes). ~50 quota units."""
        found = self.get_videos([video_id], parts="snippet,status")
        if not found:
            raise ValueError(f"video {video_id!r} not found on this channel")
        body, diff = plan_video_update(found[0], changes)
        out: dict = {"video_id": video_id, "changes": diff, "dry_run": dry_run}
        if dry_run or not diff:
            return out
        parts = ",".join(p for p in ("snippet", "status") if p in body)
        out["video"] = self._call("PUT", f"{_DATA}/videos", params={"part": parts}, json=body)
        return out

    def set_thumbnail(self, video_id: str, image: bytes, mime_type: str) -> dict:
        """Upload a custom thumbnail (JPEG or PNG, ≤ 50 MB; 1280×720 recommended).
        The channel must be allowed custom thumbnails (verified), else 403 `forbidden`.
        → raw `{items: [{default, medium, high, …}]}`. ~50 quota units."""
        if mime_type not in THUMBNAIL_MIME_TYPES:
            raise ValueError(f"a thumbnail is JPEG or PNG, got {mime_type!r}")
        if not image:
            raise ValueError("the thumbnail image is empty")
        if len(image) > THUMBNAIL_MAX_BYTES:
            raise ValueError(f"a thumbnail is at most {THUMBNAIL_MAX_BYTES // (1024 * 1024)} MB")
        return self._call("POST", f"{_UPLOAD}/thumbnails/set",
                          params={"videoId": require(video_id, "video_id"), "uploadType": "media"},
                          data=image, content_type=mime_type)

    def upload_video(self, video: bytes, mime_type: str, *, title: str,
                     description: Optional[str] = None, tags: Optional[list] = None,
                     category_id: str = "22", default_language: Optional[str] = None,
                     privacy_status: str = "private", publish_at: Optional[str] = None,
                     made_for_kids: bool = False,
                     contains_synthetic_media: Optional[bool] = None,
                     license: Optional[str] = None, embeddable: Optional[bool] = None,
                     notify_subscribers: bool = True,
                     chunk_size: int = UPLOAD_CHUNK) -> dict:
        """Upload a new video to the channel (resumable protocol) → the created video
        resource (`snippet,status`). Private by default; `publish_at` schedules a private
        video. `made_for_kids` is always sent (YouTube asks every upload for its audience).
        Uploads bucket: 100 a day per Cloud project."""
        if not video:
            raise ValueError("the video file is empty")
        if len(video) > VIDEO_MAX_BYTES:
            raise ValueError("a YouTube video is at most 256 GB")
        if not (mime_type or "").startswith("video/") and mime_type != "application/octet-stream":
            raise ValueError(f"a video upload is video/* or application/octet-stream, "
                             f"got {mime_type!r}")
        if not (title or "").strip():
            raise ValueError("title is required")
        if privacy_status not in PRIVACY_STATUSES:
            raise ValueError(f"privacy_status must be one of {', '.join(PRIVACY_STATUSES)}")
        if publish_at and privacy_status != "private":
            raise ValueError("publish_at needs privacy_status 'private' (a scheduled video)")
        if license is not None and license not in LICENSES:
            raise ValueError(f"license must be one of {', '.join(LICENSES)}")
        if tags is not None and not isinstance(tags, list):
            raise ValueError("tags must be a list of strings")
        if chunk_size <= 0 or chunk_size % (256 * 1024):
            raise ValueError("chunk_size must be a positive multiple of 256 KB")

        snippet = {"title": title, "categoryId": str(category_id)}
        for k, v in (("description", description), ("tags", tags),
                     ("defaultLanguage", default_language)):
            if v is not None:
                snippet[k] = v
        status: dict = {"privacyStatus": privacy_status,
                        "selfDeclaredMadeForKids": bool(made_for_kids)}
        for k, v in (("publishAt", publish_at), ("license", license),
                     ("embeddable", embeddable),
                     ("containsSyntheticMedia", contains_synthetic_media)):
            if v is not None:
                status[k] = v

        auth = {"Authorization": f"Bearer {self._token}"}
        total = len(video)
        init = self._session.request(
            "POST", f"{_UPLOAD}/videos",
            params={"uploadType": "resumable", "part": "snippet,status",
                    "notifySubscribers": "true" if notify_subscribers else "false"},
            headers={**auth, "X-Upload-Content-Length": str(total),
                     "X-Upload-Content-Type": mime_type},
            json={"snippet": snippet, "status": status}, timeout=self._timeout)
        self._raise(init, "POST", f"{_UPLOAD}/videos")
        session_url = init.headers.get("Location")
        if not session_url:
            raise YouTubeError(init.status_code, "no resumable session URI returned",
                               method="POST", url=f"{_UPLOAD}/videos")

        offset, failures = 0, 0
        while True:
            end = min(offset + chunk_size, total) - 1
            try:
                resp = self._session.request(
                    "PUT", session_url, data=video[offset:end + 1], allow_redirects=False,
                    headers={**auth, "Content-Type": mime_type,
                             "Content-Range": f"bytes {offset}-{end}/{total}"},
                    timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout):
                resp = None
            if resp is not None and resp.status_code in (200, 201):
                return resp.json()
            if resp is not None and resp.status_code == 308:
                offset, failures = self._received(resp), 0
                continue
            if resp is not None and resp.status_code < 500:
                self._raise(resp, "PUT", session_url)
            failures += 1
            if failures > _UPLOAD_RETRIES:
                if resp is None:
                    raise requests.ConnectionError("YouTube upload: connection lost "
                                                   f"{failures} times in a row")
                self._raise(resp, "PUT", session_url)
            # where did Google stop? an empty PUT with `bytes */total` says it
            probe = self._session.request("PUT", session_url, data=b"", allow_redirects=False,
                                      headers={**auth, "Content-Range": f"bytes */{total}"},
                                      timeout=self._timeout)
            if probe.status_code in (200, 201):
                return probe.json()
            if probe.status_code != 308:
                self._raise(probe, "PUT", session_url)
            offset = self._received(probe)

    @staticmethod
    def _received(resp: requests.Response) -> int:
        """Next byte to send after a 308: `Range: bytes=0-N` → N + 1 (no header = 0)."""
        rng = resp.headers.get("Range") or ""
        try:
            return int(rng.rsplit("-", 1)[1]) + 1 if rng else 0
        except (IndexError, ValueError):
            return 0

    def _raise(self, resp: requests.Response, method: str, url: str) -> None:
        if resp.status_code >= 400:
            try:
                payload: Any = resp.json()
            except ValueError:
                payload = resp.text
            raise YouTubeError(resp.status_code, payload, method=method, url=url)

    def reply_to_comment(self, parent_id: str, text: str) -> dict:
        """Reply, as the channel, to a top-level comment → the new comment. ~50 units."""
        if not (text or "").strip():
            raise ValueError("the reply text is empty")
        return self._call("POST", f"{_DATA}/comments", params={"part": "snippet"}, json={
            "snippet": {"parentId": require(parent_id, "parent_id"), "textOriginal": text}})

    def set_moderation_status(self, comment_ids: Iterable[str], status: str, *,
                              ban_author: bool = False) -> dict:
        """Publish, hold or reject comments on the channel's videos. `rejected` is final
        (cannot go back to `published`); `ban_author` only with `rejected`. ~50 units."""
        if status not in MODERATION_STATUSES:
            raise ValueError(f"status must be one of {', '.join(MODERATION_STATUSES)}")
        if ban_author and status != "rejected":
            raise ValueError("ban_author only goes with status 'rejected'")
        return self._call("POST", f"{_DATA}/comments/setModerationStatus", params={
            "id": _ids(comment_ids, "comment id"), "moderationStatus": status,
            "banAuthor": "true" if ban_author else None})

    def delete_comment(self, comment_id: str) -> dict:
        """Delete a comment the channel wrote. ~50 units."""
        return self._call("DELETE", f"{_DATA}/comments",
                          params={"id": require(comment_id, "comment_id")})

    def add_to_playlist(self, playlist_id: str, video_id: str, *,
                        position: Optional[int] = None) -> dict:
        """Add a video to one of the channel's playlists → the new playlist item."""
        snippet: dict = {"playlistId": require(playlist_id, "playlist_id"),
                         "resourceId": {"kind": "youtube#video",
                                        "videoId": require(video_id, "video_id")}}
        if position is not None:
            snippet["position"] = int(position)
        return self._call("POST", f"{_DATA}/playlistItems", params={"part": "snippet"},
                          json={"snippet": snippet})

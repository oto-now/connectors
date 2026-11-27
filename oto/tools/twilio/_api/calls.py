"""Calls and their recordings.

References: Call and Recording resources,
https://www.twilio.com/docs/voice/api/call-resource and
https://www.twilio.com/docs/voice/api/recording
"""
from __future__ import annotations

from typing import Optional, Sequence

from ._common import choice, e164, form, sid
from ._common import page_size as _page_size

CALL_STATUSES = ("queued", "ringing", "in-progress", "canceled", "completed", "failed",
                 "busy", "no-answer")
CALL_EVENTS = ("initiated", "ringing", "answered", "completed")
MACHINE_DETECTION = ("Enable", "DetectMessageEnd")
RECORDING_FORMATS = ("mp3", "wav")


class CallsMixin:
    def create_call(self, to: str, from_: str, *, twiml: Optional[str] = None,
                    url: Optional[str] = None, status_callback: Optional[str] = None,
                    status_callback_events: Optional[Sequence[str]] = None,
                    timeout: Optional[int] = None, time_limit: Optional[int] = None,
                    record: Optional[bool] = None, recording_status_callback: Optional[str] = None,
                    machine_detection: Optional[str] = None) -> dict:
        """Place an outbound call from `from_` (a number of this account, or a
        verified caller ID) to `to`. Exactly one of `twiml` (the document run
        once answered) or `url` (where Twilio fetches it). BILLED per minute.
        Returns the call (`sid`, `status` = queued)."""
        if (twiml is None) == (url is None):
            raise ValueError("create_call: give exactly one of `twiml` or `url`.")
        for event in status_callback_events or ():
            choice("status_callback_events", event, CALL_EVENTS)
        if timeout is not None and not 5 <= int(timeout) <= 600:
            raise ValueError("`timeout` must be between 5 and 600 seconds.")
        if time_limit is not None and not 1 <= int(time_limit) <= 86400:
            raise ValueError("`time_limit` must be between 1 and 86400 seconds.")
        data = form(To=e164(to, "to"), From=e164(from_, "from_"), Twiml=twiml, Url=url,
                    StatusCallback=status_callback,
                    StatusCallbackEvent=list(status_callback_events or []) or None,
                    Timeout=timeout, TimeLimit=time_limit, Record=record,
                    RecordingStatusCallback=recording_status_callback,
                    MachineDetection=choice("machine_detection", machine_detection, MACHINE_DETECTION))
        return self._post("/Calls.json", data)

    def get_call(self, call_sid: str) -> dict:
        """One call: `status`, `direction`, `from`, `to`, `start_time`,
        `end_time`, `duration` (seconds), `price`, `parent_call_sid`."""
        return self._get(f"/Calls/{sid(call_sid, 'CA', 'call_sid')}.json")

    def list_calls(self, *, to: Optional[str] = None, from_: Optional[str] = None,
                   status: Optional[str] = None, start_time_after: Optional[str] = None,
                   start_time_before: Optional[str] = None, parent_call_sid: Optional[str] = None,
                   page_size: Optional[int] = None) -> dict:
        """Calls of this account, most recent first. Dates are `YYYY-MM-DD`
        (UTC); `parent_call_sid` lists the legs a call dialed."""
        return self._get("/Calls.json", {
            "To": to, "From": from_, "Status": choice("status", status, CALL_STATUSES),
            "StartTime>": start_time_after, "StartTime<": start_time_before,
            "ParentCallSid": sid(parent_call_sid, "CA", "parent_call_sid") if parent_call_sid else None,
            "PageSize": _page_size(page_size)})

    def hangup_call(self, call_sid: str) -> dict:
        """End a call in progress (`completed`), or cancel one still queued or
        ringing (`canceled`): Twilio applies the one that fits."""
        call = self.get_call(call_sid)
        target = "canceled" if call.get("status") in ("queued", "ringing") else "completed"
        return self._post(f"/Calls/{sid(call_sid, 'CA', 'call_sid')}.json", form(Status=target))

    def list_recordings(self, *, call_sid: Optional[str] = None,
                        page_size: Optional[int] = None) -> dict:
        """Recordings of this account, or of one call: `sid`, `duration`,
        `status`, `channels`. The media is fetched with `recording_media`."""
        path = (f"/Calls/{sid(call_sid, 'CA', 'call_sid')}/Recordings.json" if call_sid
                else "/Recordings.json")
        return self._get(path, {"PageSize": _page_size(page_size)})

    def get_recording(self, recording_sid: str) -> dict:
        """One recording's metadata."""
        return self._get(f"/Recordings/{sid(recording_sid, 'RE', 'recording_sid')}.json")

    def recording_media(self, recording_sid: str, audio_format: str = "mp3") -> bytes:
        """The recording's audio bytes (`mp3` or `wav`), fetched with the
        account's credentials."""
        fmt = choice("audio_format", audio_format, RECORDING_FORMATS)
        rid = sid(recording_sid, "RE", "recording_sid")
        return self._send("GET", f"{self.account_path}/Recordings/{rid}.{fmt}", raw=True)

    def delete_recording(self, recording_sid: str) -> dict:
        """Delete a recording and its media. IRREVERSIBLE."""
        return self._delete(f"/Recordings/{sid(recording_sid, 'RE', 'recording_sid')}.json")

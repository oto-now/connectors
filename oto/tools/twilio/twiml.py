"""TwiML documents a host answers or sends to drive a call.

Every value is escaped (text and attributes): a phone number, a voice message
or a callback URL never changes the shape of the document.

Reference: https://www.twilio.com/docs/voice/twiml
"""
from __future__ import annotations

from typing import Optional
from xml.sax.saxutils import escape, quoteattr

_HEAD = '<?xml version="1.0" encoding="UTF-8"?>'
RECORD_MODES = ("do-not-record", "record-from-answer", "record-from-ringing",
                "record-from-answer-dual", "record-from-ringing-dual")


def _attrs(**values) -> str:
    parts = []
    for name, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(f" {name}={quoteattr(str(value))}")
    return "".join(parts)


def _say(text: Optional[str], language: Optional[str]) -> str:
    if not text:
        return ""
    return f"<Say{_attrs(language=language)}>{escape(text)}</Say>"


def empty_twiml() -> str:
    """An empty response: the webhook is acknowledged, nothing is said or sent."""
    return f"{_HEAD}<Response/>"


def bridge_twiml(to: str, *, caller_id: str, announce: Optional[str] = None,
                 language: Optional[str] = None, record: Optional[str] = "record-from-answer-dual",
                 recording_callback: Optional[str] = None, timeout: int = 30,
                 action: Optional[str] = None) -> str:
    """Dial `to` once the first leg has answered, showing `caller_id`.

    The document for a bridged call: the first leg (a person) answers, hears
    `announce` if given, then Twilio dials `to` and joins both. `record` picks
    the recording mode (`RECORD_MODES`, None for none); `recording_callback`
    receives the recording once it is ready. `action` is called when the
    second leg ends.
    """
    if record is not None and record not in RECORD_MODES:
        raise ValueError(f"`record` invalid: {record!r}. Accepted: " + ", ".join(RECORD_MODES))
    if not 5 <= int(timeout) <= 600:
        raise ValueError("`timeout` must be between 5 and 600 seconds.")
    dial = _attrs(callerId=caller_id, record=record, timeout=int(timeout), action=action,
                  recordingStatusCallback=recording_callback if record else None,
                  recordingStatusCallbackEvent="completed" if (record and recording_callback) else None)
    return (f"{_HEAD}<Response>{_say(announce, language)}"
            f"<Dial{dial}><Number>{escape(to)}</Number></Dial></Response>")


def forward_twiml(to: str, *, caller_id: Optional[str] = None, timeout: int = 25,
                  action: Optional[str] = None) -> str:
    """Forward an inbound call to `to`. `action` is called when that leg ends
    (unanswered, busy, completed): the place to fall back to a voicemail."""
    if not 5 <= int(timeout) <= 600:
        raise ValueError("`timeout` must be between 5 and 600 seconds.")
    dial = _attrs(callerId=caller_id, timeout=int(timeout), action=action)
    return f"{_HEAD}<Response><Dial{dial}><Number>{escape(to)}</Number></Dial></Response>"


def voicemail_twiml(*, greeting: Optional[str] = None, language: Optional[str] = None,
                    max_length: int = 120, recording_callback: Optional[str] = None) -> str:
    """Play `greeting`, then record a message of at most `max_length` seconds."""
    if not 1 <= int(max_length) <= 3600:
        raise ValueError("`max_length` must be between 1 and 3600 seconds.")
    rec = _attrs(maxLength=int(max_length), playBeep=True,
                 recordingStatusCallback=recording_callback,
                 recordingStatusCallbackEvent="completed" if recording_callback else None)
    return f"{_HEAD}<Response>{_say(greeting, language)}<Record{rec}/></Response>"

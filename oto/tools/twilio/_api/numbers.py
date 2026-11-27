"""Phone numbers: search what can be bought, buy, configure, release.

References: AvailablePhoneNumbers and IncomingPhoneNumbers resources,
https://www.twilio.com/docs/phone-numbers/api
"""
from __future__ import annotations

import re
from typing import Optional

from ._common import choice, e164, form, sid
from ._common import page_size as _page_size

# The path segment of each number type under AvailablePhoneNumbers/{country}.
NUMBER_TYPES = {
    "local": "Local", "mobile": "Mobile", "toll_free": "TollFree", "national": "National",
    "shared_cost": "SharedCost", "voip": "Voip", "machine_to_machine": "MachineToMachine",
}
HTTP_METHODS = ("GET", "POST")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


def _webhooks(voice_url, voice_method, voice_fallback_url, status_callback, sms_url, sms_method,
              sms_fallback_url) -> list[tuple[str, str]]:
    return form(VoiceUrl=voice_url, VoiceMethod=choice("voice_method", voice_method, HTTP_METHODS),
                VoiceFallbackUrl=voice_fallback_url, StatusCallback=status_callback,
                SmsUrl=sms_url, SmsMethod=choice("sms_method", sms_method, HTTP_METHODS),
                SmsFallbackUrl=sms_fallback_url)


class NumbersMixin:
    def search_available_numbers(self, country: str, number_type: str = "local", *,
                                 contains: Optional[str] = None, area_code: Optional[int] = None,
                                 in_locality: Optional[str] = None, in_region: Optional[str] = None,
                                 sms_enabled: Optional[bool] = None,
                                 voice_enabled: Optional[bool] = None,
                                 mms_enabled: Optional[bool] = None,
                                 page_size: Optional[int] = None) -> dict:
        """Numbers available to buy in `country` (ISO 3166 alpha-2) of
        `number_type` (`NUMBER_TYPES`). Each carries `phone_number`,
        `locality`, `region`, `capabilities` and `address_requirements`
        (none, any, local, foreign). Free; nothing is reserved."""
        code = (country or "").strip().upper()
        if not _COUNTRY.match(code):
            raise ValueError(f"`country` must be an ISO 3166 alpha-2 code such as FR; got {country!r}.")
        if number_type not in NUMBER_TYPES:
            raise ValueError(f"`number_type` invalid: {number_type!r}. Accepted: " + ", ".join(NUMBER_TYPES))
        params = {"Contains": contains, "AreaCode": area_code, "InLocality": in_locality,
                  "InRegion": in_region, "PageSize": _page_size(page_size)}
        for name, flag in (("SmsEnabled", sms_enabled), ("VoiceEnabled", voice_enabled),
                           ("MmsEnabled", mms_enabled)):
            if flag is not None:
                params[name] = "true" if flag else "false"
        return self._get(f"/AvailablePhoneNumbers/{code}/{NUMBER_TYPES[number_type]}.json", params)

    def available_number_types(self, country: str) -> list[str]:
        """The number types Twilio sells in `country` to THIS account (keys of
        `NUMBER_TYPES`), e.g. `["local"]` for France on many accounts: a type absent
        here answers 404 to `search_available_numbers`. Free."""
        code = (country or "").strip().upper()
        if not _COUNTRY.match(code):
            raise ValueError(f"`country` must be an ISO 3166 alpha-2 code such as FR; got {country!r}.")
        found = self._get(f"/AvailablePhoneNumbers/{code}.json")
        segments = set((found.get("subresource_uris") or {}).keys())
        by_segment = {v.lower(): k for k, v in NUMBER_TYPES.items()}
        by_segment.update({"toll_free": "toll_free", "shared_cost": "shared_cost",
                           "machine_to_machine": "machine_to_machine"})
        return sorted(by_segment[s] for s in segments if s in by_segment)

    def list_numbers(self, *, phone_number: Optional[str] = None,
                     friendly_name: Optional[str] = None,
                     page_size: Optional[int] = None) -> dict:
        """The numbers this account owns (`incoming_phone_numbers`), with
        their capabilities and the webhook URLs they call."""
        return self._get("/IncomingPhoneNumbers.json", {
            "PhoneNumber": phone_number, "FriendlyName": friendly_name, "PageSize": _page_size(page_size)})

    def get_number(self, number_sid: str) -> dict:
        """One owned number by its SID (`PN…`)."""
        return self._get(f"/IncomingPhoneNumbers/{sid(number_sid, 'PN', 'number_sid')}.json")

    def buy_number(self, phone_number: str, *, friendly_name: Optional[str] = None,
                   address_sid: Optional[str] = None, bundle_sid: Optional[str] = None,
                   voice_url: Optional[str] = None, voice_method: Optional[str] = None,
                   voice_fallback_url: Optional[str] = None, status_callback: Optional[str] = None,
                   sms_url: Optional[str] = None, sms_method: Optional[str] = None,
                   sms_fallback_url: Optional[str] = None) -> dict:
        """Buy `phone_number` (from a search). BILLED: the first month is
        charged at once, without proration on release. Countries with
        regulatory requirements refuse the purchase without the approved
        `bundle_sid` (and, where required, `address_sid`)."""
        data = form(PhoneNumber=e164(phone_number, "phone_number"), FriendlyName=friendly_name,
                    AddressSid=sid(address_sid, "AD", "address_sid") if address_sid else None,
                    BundleSid=sid(bundle_sid, "BU", "bundle_sid") if bundle_sid else None)
        data += _webhooks(voice_url, voice_method, voice_fallback_url, status_callback,
                          sms_url, sms_method, sms_fallback_url)
        return self._post("/IncomingPhoneNumbers.json", data)

    def update_number(self, number_sid: str, *, friendly_name: Optional[str] = None,
                      voice_url: Optional[str] = None, voice_method: Optional[str] = None,
                      voice_fallback_url: Optional[str] = None, status_callback: Optional[str] = None,
                      sms_url: Optional[str] = None, sms_method: Optional[str] = None,
                      sms_fallback_url: Optional[str] = None) -> dict:
        """Change an owned number's name or webhook URLs. Only the arguments
        given are sent; an empty string clears that URL."""
        data = form(FriendlyName=friendly_name) + _webhooks(
            voice_url, voice_method, voice_fallback_url, status_callback,
            sms_url, sms_method, sms_fallback_url)
        if not data:
            raise ValueError("update_number: give at least one field to change.")
        return self._post(f"/IncomingPhoneNumbers/{sid(number_sid, 'PN', 'number_sid')}.json", data)

    def release_number(self, number_sid: str) -> dict:
        """Release an owned number. IRREVERSIBLE: the number returns to the
        pool and may never be bought back; the month already paid is not
        refunded."""
        return self._delete(f"/IncomingPhoneNumbers/{sid(number_sid, 'PN', 'number_sid')}.json")

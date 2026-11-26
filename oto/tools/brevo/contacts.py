"""Brevo — contacts, attributes, lists, folders, segments.

Brevo vocabulary: a **contact** carries `attributes` (typed columns, declared
at account level); it belongs to **lists**; a list lives in a **folder**
(`folderId` is mandatory at creation); a **segment** is a dynamic list
defined by a filter (read-only through the API).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._base import _BrevoBase


class ContactsMixin(_BrevoBase):

    # --- Contacts -----------------------------------------------------------

    def list_contacts(
        self,
        limit: int = 50,
        offset: int = 0,
        modified_since: Optional[str] = None,
        created_since: Optional[str] = None,
        sort: Optional[str] = None,
        segment_id: Optional[int] = None,
        list_ids: Optional[List[int]] = None,
        ids: Optional[List[int]] = None,
        filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List contacts (paginated).

        Args:
            limit: max 1000 on Brevo's side.
            modified_since / created_since: ISO 8601 UTC (`YYYY-MM-DDTHH:mm:ss.SSSZ`).
            sort: `asc` | `desc` (default `desc`, by creation date).
            segment_id: filter by segment. **Mutually exclusive with `list_ids`.**
            ids: max 20 contact ids.
            filter: filter on attributes, `equals` operator only
                (e.g. `equals(FIRSTNAME,"Alex")`).
        """
        params = self._clean({
            "limit": min(limit, 1000), "offset": offset,
            "modifiedSince": modified_since, "createdSince": created_since,
            "sort": sort, "segmentId": segment_id, "listIds": list_ids,
            "ids": ids, "filter": filter,
        })
        return self._request("GET", "/contacts", params=params)

    def get_contact(
        self,
        identifier: str,
        identifier_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch a contact.

        Args:
            identifier: email, numeric id, phone or EXT_ID depending on `identifier_type`.
            identifier_type: `email_id` | `contact_id` | `phone_id` | `ext_id` |
                `whatsapp_id` | `landline_number_id`. Brevo default = email.
        """
        params = self._clean({
            "identifierType": identifier_type,
            "startDate": start_date, "endDate": end_date,
        })
        return self._request("GET", f"/contacts/{identifier}", params=params or None)

    def upsert_contact(
        self,
        email: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
        list_ids: Optional[List[int]] = None,
        update_enabled: bool = True,
        ext_id: Optional[str] = None,
        email_blacklisted: Optional[bool] = None,
        sms_blacklisted: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Create a contact — or update it if `update_enabled` (default).

        Returns `{"id": …}` on creation; **empty body (204) on an update**.
        """
        body = self._clean({
            "email": email, "attributes": attributes, "listIds": list_ids,
            "updateEnabled": update_enabled, "ext_id": ext_id,
            "emailBlacklisted": email_blacklisted, "smsBlacklisted": sms_blacklisted,
        })
        return self._request("POST", "/contacts", json=body)

    def update_contact(
        self,
        identifier: str,
        attributes: Optional[Dict[str, Any]] = None,
        list_ids: Optional[List[int]] = None,
        unlink_list_ids: Optional[List[int]] = None,
        identifier_type: Optional[str] = None,
        email_blacklisted: Optional[bool] = None,
        sms_blacklisted: Optional[bool] = None,
        ext_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing contact. `unlink_list_ids` removes it from those lists.

        Prefer over `upsert_contact` when targeting by id/ext_id, or to
        unsubscribe from a list. Returns an empty body (204) on success.
        """
        body = self._clean({
            "attributes": attributes, "listIds": list_ids,
            "unlinkListIds": unlink_list_ids, "ext_id": ext_id,
            "emailBlacklisted": email_blacklisted, "smsBlacklisted": sms_blacklisted,
        })
        params = self._clean({"identifierType": identifier_type})
        return self._request(
            "PUT", f"/contacts/{identifier}", json=body, params=params or None)

    def delete_contact(self, identifier: str,
                       identifier_type: Optional[str] = None) -> Dict[str, Any]:
        """Delete a contact for good (attributes, list memberships, history).

        `identifier` is an email by default; `identifier_type` says otherwise.
        Returns an empty body (204) on success.
        """
        params = self._clean({"identifierType": identifier_type})
        return self._request("DELETE", f"/contacts/{identifier}", params=params or None)

    def contact_campaign_stats(
        self, identifier: str,
        start_date: Optional[str] = None, end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """A contact's campaign stats (opens, clicks, bounces…)."""
        params = self._clean({"startDate": start_date, "endDate": end_date})
        return self._request(
            "GET", f"/contacts/{identifier}/campaignStats", params=params or None)

    def import_contacts(
        self,
        list_ids: Optional[List[int]] = None,
        json_body: Optional[List[Dict[str, Any]]] = None,
        file_url: Optional[str] = None,
        file_body: Optional[str] = None,
        update_existing_contacts: bool = True,
        empty_contacts_attributes: bool = False,
        new_list: Optional[Dict[str, Any]] = None,
        notify_url: Optional[str] = None,
        disable_notification: bool = True,
    ) -> Dict[str, Any]:
        """**Asynchronous** bulk import — returns `{"processId": …}`.

        The way to go beyond 150 contacts (instead of `add_to_list`).
        Provide **one** source: `json_body` (list of `{"email", "attributes", …}`),
        `file_url` (remote CSV) or `file_body` (inline CSV, `;` as separator).

        Args:
            new_list: `{"listName": …, "folderId": …}` to create the list on the fly.
            empty_contacts_attributes: `True` overwrites with empty values the
                attributes missing from the file. Destructive — leave `False`.
        """
        body = self._clean({
            "listIds": list_ids, "jsonBody": json_body, "fileUrl": file_url,
            "fileBody": file_body, "updateExistingContacts": update_existing_contacts,
            "emptyContactsAttributes": empty_contacts_attributes,
            "newList": new_list, "notifyUrl": notify_url,
            "disableNotification": disable_notification,
        })
        return self._request("POST", "/contacts/import", json=body)

    def export_contacts(
        self,
        contact_filter: Optional[Dict[str, Any]] = None,
        export_attributes: Optional[List[str]] = None,
        notify_url: Optional[str] = None,
        disable_notification: bool = True,
    ) -> Dict[str, Any]:
        """**Asynchronous** contact export — returns `{"processId": …}`.

        Args:
            contact_filter: `{"listIds": [1]}` | `{"segmentId": 2}` |
                `{"emailBlacklisted": true}`. Default = all contacts.
        """
        body = self._clean({
            "customContactFilter": contact_filter or {"emailBlacklisted": False},
            "exportAttributes": export_attributes, "notifyUrl": notify_url,
            "disableNotification": disable_notification,
        })
        return self._request("POST", "/contacts/export", json=body)

    # --- Attributes & segments ----------------------------------------------

    def list_attributes(self) -> Dict[str, Any]:
        """List the account's contact attributes (name, category, type)."""
        return self._request("GET", "/contacts/attributes")

    def list_segments(self, limit: int = 50, offset: int = 0,
                      sort: Optional[str] = None) -> Dict[str, Any]:
        """List segments (dynamic lists). Read-only through the API."""
        params = self._clean({"limit": limit, "offset": offset, "sort": sort})
        return self._request("GET", "/contacts/segments", params=params)

    # --- Lists & folders -----------------------------------------------------

    def list_lists(self, limit: int = 50, offset: int = 0,
                   sort: Optional[str] = None,
                   folder_id: Optional[int] = None) -> Dict[str, Any]:
        """List contact lists, for the account or for one folder."""
        params = self._clean({"limit": limit, "offset": offset, "sort": sort})
        path = f"/contacts/folders/{folder_id}/lists" if folder_id else "/contacts/lists"
        return self._request("GET", path, params=params)

    def get_list(self, list_id: int) -> Dict[str, Any]:
        """A list's details (name, folder, contact count, blacklisted)."""
        return self._request("GET", f"/contacts/lists/{int(list_id)}")

    def list_contacts_of_list(
        self, list_id: int, limit: int = 50, offset: int = 0,
        modified_since: Optional[str] = None, sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Contacts of a list (paginated)."""
        params = self._clean({
            "limit": min(limit, 500), "offset": offset,
            "modifiedSince": modified_since, "sort": sort,
        })
        return self._request(
            "GET", f"/contacts/lists/{int(list_id)}/contacts", params=params)

    def create_list(self, name: str, folder_id: int) -> Dict[str, Any]:
        """Create a list. `folder_id` is **mandatory** on Brevo's side (see `list_folders`)."""
        return self._request("POST", "/contacts/lists",
                             json={"name": name, "folderId": int(folder_id)})

    def update_list(self, list_id: int, name: Optional[str] = None,
                    folder_id: Optional[int] = None) -> Dict[str, Any]:
        """Rename a list or move it to another folder."""
        body = self._clean({"name": name, "folderId": folder_id})
        return self._request("PUT", f"/contacts/lists/{int(list_id)}", json=body)

    def delete_list(self, list_id: int) -> Dict[str, Any]:
        """Delete a list for good; its contacts stay in the account. Empty body (204) on success."""
        return self._request("DELETE", f"/contacts/lists/{int(list_id)}")

    def _list_membership(self, list_id: int, action: str, emails, ids, ext_ids, all_):
        given = [x for x in (emails, ids, ext_ids) if x]
        if len(given) != 1 and not all_:
            raise ValueError(
                "Provide exactly ONE identifier type (emails, ids or ext_ids).")
        body = self._clean({
            "emails": emails, "ids": ids, "extIds": ext_ids, "all": all_ or None})
        return self._request(
            "POST", f"/contacts/lists/{int(list_id)}/contacts/{action}", json=body)

    def add_to_list(
        self, list_id: int, emails: Optional[List[str]] = None,
        ids: Optional[List[int]] = None, ext_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Add EXISTING contacts to a list.

        **Max 150 contacts per call**, and a SINGLE identifier type at a time.
        Beyond that → `import_contacts`. Returns `{contacts: {success: [], failure: []}}`.
        """
        return self._list_membership(list_id, "add", emails, ids, ext_ids, None)

    def remove_from_list(
        self, list_id: int, emails: Optional[List[str]] = None,
        ids: Optional[List[int]] = None, ext_ids: Optional[List[str]] = None,
        all_contacts: bool = False,
    ) -> Dict[str, Any]:
        """Remove contacts from a list (does not delete the contacts).

        **Max 150 per call**, a single identifier type. `all_contacts=True`
        empties the list.
        """
        return self._list_membership(
            list_id, "remove", emails, ids, ext_ids, all_contacts or None)

    def list_folders(self, limit: int = 50, offset: int = 0,
                     sort: Optional[str] = None) -> Dict[str, Any]:
        """List list folders (their `id` is used by `create_list`)."""
        params = self._clean({"limit": limit, "offset": offset, "sort": sort})
        return self._request("GET", "/contacts/folders", params=params)

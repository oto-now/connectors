"""Google Chat API client using OAuth2 user credentials.

Acts as the authenticated user (not a Chat bot): messages are posted under
the user's identity into spaces the user belongs to. Direct messages are
resolved via `spaces.findDirectMessage`, so a DM space with the recipient
must already exist (Chat does not let a user create a brand-new DM space
through the API).
"""

from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from oto.tools.common.credentials import require

# Least privilege: one scope per call this client makes, nothing that edits or
# deletes (no `chat.messages`, which also allows update, delete and reactions).
# chat.spaces.readonly   → spaces.list + spaces.findDirectMessage
# chat.messages.readonly → spaces.messages.list
# chat.messages.create   → spaces.messages.create
SCOPES = [
    'https://www.googleapis.com/auth/chat.spaces.readonly',
    'https://www.googleapis.com/auth/chat.messages.readonly',
    'https://www.googleapis.com/auth/chat.messages.create',
]


class ChatClientError(Exception):
    """Google Chat API error."""


class ChatClient:
    """Google Chat API client.

    Args:
        credentials: OAuth2 user credentials, provided by the consumer (required).
    """

    def __init__(self, credentials: Optional[Credentials] = None):
        self.service = build('chat', 'v1', credentials=require(credentials, 'GOOGLE_CREDENTIALS'))

    def list_spaces(self, filter_: Optional[str] = None, max_results: int = 100) -> list[dict]:
        """List spaces (rooms + DMs) the authenticated user belongs to.

        Args:
            filter_: Chat API filter, e.g. 'spaceType = "DIRECT_MESSAGE"'.
            max_results: Cap on spaces returned (paginates transparently).
        """
        spaces: list[dict] = []
        page_token = None
        while len(spaces) < max_results:
            kwargs = {'pageSize': min(1000, max_results - len(spaces))}
            if filter_:
                kwargs['filter'] = filter_
            if page_token:
                kwargs['pageToken'] = page_token
            resp = self.service.spaces().list(**kwargs).execute()
            for s in resp.get('spaces', []):
                spaces.append({
                    'name': s.get('name', ''),
                    'displayName': s.get('displayName', ''),
                    'spaceType': s.get('spaceType', ''),
                    'singleUserBotDm': s.get('singleUserBotDm', False),
                })
            page_token = resp.get('nextPageToken')
            if not page_token:
                break
        return spaces

    def find_dm(self, user: str) -> str:
        """Resolve the direct-message space with a given user.

        Args:
            user: The recipient's email (alias for the user id under user auth)
                or a People/Directory user id.

        Returns:
            The DM space resource name, e.g. 'spaces/AAAA...'.

        Raises:
            ChatClientError: If no DM space exists with that user.
        """
        name = user if user.startswith('users/') else f'users/{user}'
        try:
            space = self.service.spaces().findDirectMessage(name=name).execute()
        except Exception as e:
            raise ChatClientError(
                f"No direct message space found with {user!r}. "
                f"Open a DM with them in Google Chat once, then retry. ({e})"
            )
        return space['name']

    def send(self, space: str, text: str) -> dict:
        """Post a text message into a space.

        Args:
            space: Space resource name ('spaces/XXXX').
            text: Message text (Chat basic formatting supported: *bold*, _italic_).
        """
        msg = self.service.spaces().messages().create(
            parent=space, body={'text': text},
        ).execute()
        return {
            'name': msg.get('name', ''),
            'space': space,
            'createTime': msg.get('createTime', ''),
            'text': msg.get('text', ''),
        }

    def send_dm(self, user: str, text: str) -> dict:
        """Send a direct message to a user by email/id (resolves the DM space)."""
        space = self.find_dm(user)
        result = self.send(space, text)
        result['to'] = user
        return result

    def list_messages(self, space: str, max_results: int = 20) -> list[dict]:
        """List recent messages in a space (most recent first)."""
        messages: list[dict] = []
        page_token = None
        while len(messages) < max_results:
            kwargs = {
                'parent': space,
                'pageSize': min(1000, max_results - len(messages)),
                'orderBy': 'createTime desc',
            }
            if page_token:
                kwargs['pageToken'] = page_token
            resp = self.service.spaces().messages().list(**kwargs).execute()
            for m in resp.get('messages', []):
                sender = m.get('sender', {})
                messages.append({
                    'name': m.get('name', ''),
                    'sender': sender.get('name', ''),
                    'createTime': m.get('createTime', ''),
                    'text': m.get('text', ''),
                })
            page_token = resp.get('nextPageToken')
            if not page_token:
                break
        return messages

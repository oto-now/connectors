"""Instantly call families, composed into `InstantlyClient`.

One module per API domain. Contract details: `../client.py`.
"""

from .campaigns import _CampaignsMixin
from .leads import _LeadsMixin
from .unibox import _UniboxMixin
from .webhooks import _WebhooksMixin

__all__ = ["_CampaignsMixin", "_LeadsMixin", "_UniboxMixin", "_WebhooksMixin"]

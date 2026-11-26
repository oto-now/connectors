"""Addresses that open content without a LinkedIn login, dropped from the answers.

No such address reaches the caller: the picture of a member, a company, a
school or an organisation, the picture of a conversation, and the tracking
pixel of a job posting open for anyone who holds them. Each answer drops them
by dotted path (`OPEN_ADDRESS_FIELDS`), keyed by the function of the
description `linkedin_unipile` that describes the call, and the description
strips the same paths (`output.strip`). Profile addresses (`profile_url`),
behind a LinkedIn login, stay; so does an address of the Unipile API itself
(`private_picture_download_url`, a message attachment), which needs the key.

The paths come from the answers of the Unipile v2 reference: relative to the
answer, or to each element of `data` for a list.
"""

from __future__ import annotations

from typing import Any

# A member's profile: the profile of the connected account and of any member.
_PROFILE = (
    "public_picture_url",
    "public_picture_url_large",
    "background_picture_url",
    "specifics.experience.company.picture_url",
    "specifics.education.school.picture_url",
    "specifics.certifications.organization.picture_url",
    "specifics.volunteer_experience.organization.picture_url",
    "specifics.projects.contributors.public_picture_url",
    "specifics.recommendations.given.user.public_picture_url",
    "specifics.recommendations.received.user.public_picture_url",
    "specifics.interests.companies.public_picture_url",
    "specifics.interests.schools.public_picture_url",
    "specifics.interests.groups.public_picture_url",
    "specifics.interests.newsletters.public_picture_url",
    "specifics.interests.top_voices.public_picture_url",
    "specifics.relation_request.user.public_picture_url",
    "specifics.recruiting_profile.events.last_updated_by.public_picture_url",
    "specifics.recruiting_profile.notes.author.public_picture_url",
    "specifics.recruiting_profile.notes.comments.author.public_picture_url",
)
# A member or a company as a search lists it, classic or Sales Navigator companies.
_LISTED_PICTURES = ("public_picture_url", "public_picture_url_large")
_SALES_NAVIGATOR_LEAD = (
    "public_picture_url",
    "public_picture_url_large",
    "work_experience.company.picture_url",
    "education.school.picture_url",
    "volunteering.organization.picture_url",
)
_RECRUITER_CANDIDATE = (
    "public_picture_url",
    "public_picture_url_large",
    "work_experience.company.picture_url",
    "education.school.picture_url",
    "certifications.organization.picture_url",
    "volunteering.organization.picture_url",
    "projects.contributors.public_picture_url",
    "recommendations.user.public_picture_url",
)
# A post, alone or listed among the posts of a member or a company page.
_POST = (
    "author.public_picture_url",
    "reposted_by.public_picture_url",
    "event.public_picture_url",
    "specifics.article.picture_url",
    "specifics.job_posting.company.picture_url",
    "quoted_post.author.public_picture_url",
    "quoted_post.event.public_picture_url",
    "quoted_post.specifics.article.picture_url",
    "quoted_post.specifics.job_posting.company.picture_url",
)
_APPLICANT = (
    "profile.public_picture_url",
    "profile.work_experience.company.picture_url",
    "profile.educations.school.picture_url",
)

OPEN_ADDRESS_FIELDS: dict[str, tuple[str, ...]] = {
    "get_me": _PROFILE,
    "get_profile": _PROFILE,
    "get_company": (
        "public_picture_url",
        "acquired_by.public_picture_url",
        "funding.crunchbase.last_round.lead_investors.logo_url",
    ),
    "list_relations": ("user.public_picture_url",),
    "list_invitations": ("user.public_picture_url",),
    "send_invitation": ("user.public_picture_url",),
    "get_post": _POST,
    "list_member_posts": _POST,
    "list_chats": (
        "image_url",
        "public_picture_url",
        "user.public_picture_url",
        "participants.user.public_picture_url",
        "last_message.sender.public_picture_url",
        "last_message.quoted.sender.public_picture_url",
        "last_message.forwarded.sender.public_picture_url",
    ),
    "list_chat_messages": (
        "sender.public_picture_url",
        "quoted.sender.public_picture_url",
        "forwarded.sender.public_picture_url",
    ),
    "search_people": _LISTED_PICTURES,
    "search_companies": _LISTED_PICTURES,
    "search_sales_navigator_people": _SALES_NAVIGATOR_LEAD,
    "search_sales_navigator_companies": _LISTED_PICTURES,
    "search_recruiter_people": _RECRUITER_CANDIDATE,
    "search_by_url": (
        "public_picture_url",
        "public_picture_url_large",
        "company.public_picture_url",
        "author.public_picture_url",
        "reposted_by.public_picture_url",
        "event.public_picture_url",
        "quoted_post.author.public_picture_url",
        "quoted_post.event.public_picture_url",
    ),
    "search_sales_navigator_by_url": _SALES_NAVIGATOR_LEAD + (
        "associated_account.public_picture_url",
        "recommended_lead.public_picture_url",
        "recommended_lead.public_picture_url_large",
        "recommended_lead.work_experience.company.picture_url",
        "recommended_lead.education.school.picture_url",
        "recommended_lead.volunteering.organization.picture_url",
    ),
    "search_recruiter_by_url": _RECRUITER_CANDIDATE + tuple(f"profile.{p}" for p in _RECRUITER_CANDIDATE),
    "get_job_posting": ("company.public_picture_url", "tracking_pixel_url"),
    "list_job_applicants": _APPLICANT,
    "get_job_applicant": _APPLICANT,
    "list_hiring_projects": ("owner.public_picture_url",),
}


def drop(node: Any, keys: list[str]) -> None:
    """Drop the key at the end of `keys`, walking through lists on the way."""
    if isinstance(node, list):
        for item in node:
            drop(item, keys)
    elif isinstance(node, dict):
        if len(keys) == 1:
            node.pop(keys[0], None)
        elif keys[0] in node:
            drop(node[keys[0]], keys[1:])


def without_open_addresses(function: str, answer: Any, listed: bool = False) -> Any:
    """The answer of `function` without its open addresses (`OPEN_ADDRESS_FIELDS`):
    from the answer itself, or from each element of `data` when `listed`."""
    target = answer.get("data") if listed and isinstance(answer, dict) else answer
    for path in OPEN_ADDRESS_FIELDS[function]:
        drop(target, path.split("."))
    return answer

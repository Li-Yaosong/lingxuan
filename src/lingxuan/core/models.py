"""Domain helper functions, value objects, and rule constants.

Pure logic extracted from MVP — no IO, no framework dependencies.
Only depends on stdlib + protocols/.
"""

from __future__ import annotations

import uuid

from lingxuan.protocols.repositories import UserProfile

# ---------------------------------------------------------------------------
# Relation constants (social graph edge types)
# ---------------------------------------------------------------------------

RELATION_INTRODUCED_AS = "introduced_as"
RELATION_ALSO_KNOWN_AS = "also_known_as"
RELATION_FRIEND_OF = "friend_of"
RELATION_SELF_IDENTIFIED_AS = "self_identified_as"

# ---------------------------------------------------------------------------
# Fact category constants
# ---------------------------------------------------------------------------

FACT_CATEGORY_IDENTITY = "identity"
FACT_CATEGORY_PREFERENCE = "preference"
FACT_CATEGORY_SKILL = "skill"
FACT_CATEGORY_RELATION = "relation"
FACT_CATEGORY_GENERAL = "general"

# ---------------------------------------------------------------------------
# Relationship stage computation (reproduces MVP _compute_stage)
# ---------------------------------------------------------------------------

_STAGE_LABELS: dict[str, str] = {
    "stranger": "陌生",
    "acquaintance": "认识",
    "familiar": "熟悉",
    "close": "亲近",
}


def compute_stage(
    profile: UserProfile,
    *,
    close_threshold: int = 30,
    familiar_threshold: int = 10,
    acquaintance_threshold: int = 3,
) -> str:
    """Compute relationship stage from a UserProfile.

    Priority: close > familiar (private+group or count) > acquaintance > stranger.
    """
    count = profile.interaction_count
    non_identity_facts = any(
        f.active and f.category != FACT_CATEGORY_IDENTITY for f in profile.facts
    )

    if count >= close_threshold:
        return "close"
    if profile.seen_in_private and profile.seen_in_group:
        return "familiar"
    if count >= familiar_threshold:
        return "familiar"
    if count >= acquaintance_threshold or non_identity_facts:
        return "acquaintance"
    return "stranger"


def stage_label(stage: str) -> str:
    """Return Chinese label for a relationship stage."""
    return _STAGE_LABELS.get(stage, stage)


# ---------------------------------------------------------------------------
# Display name
# ---------------------------------------------------------------------------


def display_name(profile: UserProfile) -> str:
    """Return the best display name: preferred_name > first alias > empty."""
    if profile.preferred_name:
        return profile.preferred_name
    if profile.aliases:
        return profile.aliases[0]
    return ""


# ---------------------------------------------------------------------------
# Fact ID generation
# ---------------------------------------------------------------------------


def new_fact_id() -> str:
    """Generate an 8-char hex fact ID."""
    return uuid.uuid4().hex[:8]

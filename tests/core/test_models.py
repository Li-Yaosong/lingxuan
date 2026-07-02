"""Tests for core/models.py — domain helpers and rule constants."""

from __future__ import annotations

import re

import pytest

from lingxuan.core.models import (
    FACT_CATEGORY_GENERAL,
    FACT_CATEGORY_IDENTITY,
    FACT_CATEGORY_PREFERENCE,
    FACT_CATEGORY_RELATION,
    FACT_CATEGORY_SKILL,
    RELATION_ALSO_KNOWN_AS,
    RELATION_FRIEND_OF,
    RELATION_INTRODUCED_AS,
    RELATION_SELF_IDENTIFIED_AS,
    compute_stage,
    display_name,
    new_fact_id,
    stage_label,
)
from lingxuan.protocols.repositories import UserFact, UserProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile(
    *,
    interaction_count: int = 0,
    seen_in_private: bool = False,
    seen_in_group: bool = False,
    facts: list[UserFact] | None = None,
    preferred_name: str = "",
    aliases: list[str] | None = None,
) -> UserProfile:
    return UserProfile(
        user_id=1,
        interaction_count=interaction_count,
        seen_in_private=seen_in_private,
        seen_in_group=seen_in_group,
        facts=facts or [],
        preferred_name=preferred_name,
        aliases=aliases or [],
    )


# ---------------------------------------------------------------------------
# compute_stage — 4 branches + boundary values
# ---------------------------------------------------------------------------


class TestComputeStage:
    """Reproduces MVP _compute_stage logic."""

    # --- close ---

    def test_close_at_threshold(self) -> None:
        assert compute_stage(_profile(interaction_count=30)) == "close"

    def test_close_above_threshold(self) -> None:
        assert compute_stage(_profile(interaction_count=100)) == "close"

    # --- familiar (seen_in_private + seen_in_group) ---

    def test_familiar_both_channels_low_count(self) -> None:
        assert compute_stage(_profile(interaction_count=2, seen_in_private=True, seen_in_group=True)) == "familiar"

    # --- familiar (interaction_count >= 10) ---

    def test_familiar_at_threshold(self) -> None:
        assert compute_stage(_profile(interaction_count=10)) == "familiar"

    def test_familiar_above_threshold_not_close(self) -> None:
        assert compute_stage(_profile(interaction_count=29)) == "familiar"

    # --- acquaintance (interaction_count >= 3) ---

    def test_acquaintance_at_threshold(self) -> None:
        assert compute_stage(_profile(interaction_count=3)) == "acquaintance"

    def test_acquaintance_below_familiar_threshold(self) -> None:
        assert compute_stage(_profile(interaction_count=9)) == "acquaintance"

    # --- acquaintance (non-identity active fact) ---

    def test_acquaintance_via_non_identity_fact(self) -> None:
        facts = [UserFact(id="a", content="likes cats", category="preference", active=True)]
        assert compute_stage(_profile(interaction_count=0, facts=facts)) == "acquaintance"

    def test_identity_fact_does_not_promote(self) -> None:
        facts = [UserFact(id="b", content="called 小明", category="identity", active=True)]
        assert compute_stage(_profile(interaction_count=0, facts=facts)) == "stranger"

    def test_inactive_fact_ignored(self) -> None:
        facts = [UserFact(id="c", content="likes cats", category="preference", active=False)]
        assert compute_stage(_profile(interaction_count=0, facts=facts)) == "stranger"

    # --- stranger ---

    def test_stranger_count_below_3(self) -> None:
        assert compute_stage(_profile(interaction_count=2)) == "stranger"

    def test_stranger_zero(self) -> None:
        assert compute_stage(_profile()) == "stranger"

    # --- only private, no group ---

    def test_private_only_not_familiar(self) -> None:
        assert compute_stage(_profile(interaction_count=5, seen_in_private=True)) == "acquaintance"

    # --- only group, no private ---

    def test_group_only_not_familiar(self) -> None:
        assert compute_stage(_profile(interaction_count=5, seen_in_group=True)) == "acquaintance"

    # --- custom thresholds ---

    def test_custom_close_threshold(self) -> None:
        assert compute_stage(_profile(interaction_count=50), close_threshold=50) == "close"
        assert compute_stage(_profile(interaction_count=49), close_threshold=50) == "familiar"

    def test_custom_familiar_threshold(self) -> None:
        assert compute_stage(_profile(interaction_count=5), familiar_threshold=5) == "familiar"

    def test_custom_acquaintance_threshold(self) -> None:
        assert compute_stage(_profile(interaction_count=1), acquaintance_threshold=1) == "acquaintance"


# ---------------------------------------------------------------------------
# stage_label
# ---------------------------------------------------------------------------


class TestStageLabel:
    def test_known_labels(self) -> None:
        assert stage_label("stranger") == "陌生"
        assert stage_label("acquaintance") == "认识"
        assert stage_label("familiar") == "熟悉"
        assert stage_label("close") == "亲近"

    def test_unknown_falls_back(self) -> None:
        assert stage_label("unknown") == "unknown"


# ---------------------------------------------------------------------------
# display_name
# ---------------------------------------------------------------------------


class TestDisplayName:
    def test_preferred_name(self) -> None:
        assert display_name(_profile(preferred_name="小明")) == "小明"

    def test_first_alias(self) -> None:
        assert display_name(_profile(aliases=["阿明", "明明"])) == "阿明"

    def test_preferred_over_alias(self) -> None:
        assert display_name(_profile(preferred_name="小明", aliases=["阿明"])) == "小明"

    def test_empty_when_nothing(self) -> None:
        assert display_name(_profile()) == ""


# ---------------------------------------------------------------------------
# new_fact_id
# ---------------------------------------------------------------------------


class TestNewFactId:
    def test_length_8(self) -> None:
        assert len(new_fact_id()) == 8

    def test_hex_chars(self) -> None:
        assert re.fullmatch(r"[0-9a-f]{8}", new_fact_id())

    def test_unique(self) -> None:
        ids = {new_fact_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_relation_constants(self) -> None:
        assert RELATION_INTRODUCED_AS == "introduced_as"
        assert RELATION_ALSO_KNOWN_AS == "also_known_as"
        assert RELATION_FRIEND_OF == "friend_of"
        assert RELATION_SELF_IDENTIFIED_AS == "self_identified_as"

    def test_fact_category_constants(self) -> None:
        assert FACT_CATEGORY_IDENTITY == "identity"
        assert FACT_CATEGORY_PREFERENCE == "preference"
        assert FACT_CATEGORY_SKILL == "skill"
        assert FACT_CATEGORY_RELATION == "relation"
        assert FACT_CATEGORY_GENERAL == "general"

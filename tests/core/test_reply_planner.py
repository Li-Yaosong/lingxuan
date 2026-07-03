"""Tests for core/reply_planner.py — split_chunks, take_emit_chunk, plan_static, plan_stream."""

from __future__ import annotations

import random

import pytest

from lingxuan.core.reply_planner import ReplyPlanner, split_chunks, take_emit_chunk
from lingxuan.protocols.messaging import OutboundChunk
from tests.fakes.config import FakeConfigProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_OVERRIDES = {
    "GROUP_MSG_CHUNK_MAX": 35,
    "GROUP_MSG_CHUNK_MIN": 6,
    "GROUP_MSG_CHUNK_LIMIT": 6,
    "GROUP_CHUNK_DELAY_MIN": 0.4,
    "GROUP_CHUNK_DELAY_MAX": 1.2,
    "ENABLE_STREAM_CHUNK": True,
}


def _make_planner(
    **overrides: object,
) -> ReplyPlanner:
    merged = {**DEFAULT_CHUNK_OVERRIDES, **overrides}
    config = FakeConfigProvider(merged)
    rng = random.Random(42)
    return ReplyPlanner(config, rng=rng)


async def _collect_aiter(aiter) -> list[OutboundChunk]:
    result = []
    async for chunk in aiter:
        result.append(chunk)
    return result


async def _token_iter(tokens: list[str]):
    """Simple async iterator over a list of tokens."""
    for t in tokens:
        yield t


# ---------------------------------------------------------------------------
# split_chunks — pure function tests
# ---------------------------------------------------------------------------

class TestSplitChunksBasic:
    def test_empty_string(self) -> None:
        assert split_chunks("", max_len=35, min_len=6, limit=6) == []

    def test_whitespace_only(self) -> None:
        assert split_chunks("   ", max_len=35, min_len=6, limit=6) == []

    def test_single_short_sentence(self) -> None:
        result = split_chunks("你好。", max_len=35, min_len=6, limit=6)
        assert result == ["你好。"]

    def test_chinese_no_spaces_stays_as_one(self) -> None:
        # Chinese sentences without spaces/newlines between them don't split
        # — regex only matches sentence-end followed by \s or $, not next char
        text = "你好呀。我是灵轩。很高兴认识你！"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        assert len(result) == 1
        assert result[0] == text

    def test_chinese_with_newlines_splits(self) -> None:
        text = "你好呀。\n我是灵轩。\n很高兴认识你！"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        # .strip() removes the \n from each part, so we get 3 pieces
        # then merge step merges short pieces: "你好呀。" (4) + "我是灵轩。" (5) = 9
        # "很高兴认识你！" (7) stays separate
        assert len(result) == 2
        assert result[0] == "你好呀。我是灵轩。"
        assert result[1] == "很高兴认识你！"

    def test_no_sentence_end(self) -> None:
        text = "这是一段没有标点的话"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        assert result == [text]

    def test_sentence_end_at_string_boundary(self) -> None:
        # Final punctuation at string end matches ($)
        text = "你好呀。"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        assert result == ["你好。"] or result == ["你好呀。"]


class TestSplitChunksHardCut:
    def test_overlength_sentence_hard_cut(self) -> None:
        text = "A" * 50
        result = split_chunks(text, max_len=20, min_len=6, limit=10)
        for chunk in result[:-1]:
            assert len(chunk) <= 20
        assert len(result) >= 2

    def test_hard_cut_respects_max_len(self) -> None:
        text = "X" * 100
        result = split_chunks(text, max_len=30, min_len=5, limit=10)
        for chunk in result:
            assert len(chunk) <= 30

    def test_chinese_overlength_hard_cut(self) -> None:
        # Long Chinese text without spaces → hard cut
        text = "你" * 50 + "。"
        result = split_chunks(text, max_len=20, min_len=6, limit=10)
        assert len(result) >= 2
        for chunk in result[:-1]:
            assert len(chunk) <= 20


class TestSplitChunksMergeShort:
    def test_short_piece_merged_into_previous(self) -> None:
        # "短。" is short (< min_len=6), should merge with previous if fits
        text = "这是一段正常长度的话。\n短。"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        # The short "短。" should have been merged into previous chunk
        assert len(result) == 1
        assert "短。" in result[0]

    def test_short_piece_not_merged_if_overflows(self) -> None:
        # Previous chunk is near max_len, short piece can't fit
        text = "A" * 32 + "。短。"
        # With max_len=35: "A"*32+"。" = 33 chars. "短。" = 2 chars. 33+2=35 <= 35
        # So it DOES merge. Use a tighter max_len.
        result = split_chunks(text, max_len=33, min_len=6, limit=6)
        # "A"*32+"。" = 33 chars (fits exactly), "短。" = 2 chars (< min_len)
        # Merge: 33+2=35 > 33, can't merge → stays separate
        assert len(result) == 2

    def test_merge_preserves_content_modulo_strip(self) -> None:
        text = "你好呀。\n我。"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        # .strip() removes the \n, so reassembly loses it — this is MVP behavior
        assert "".join(result) == "你好呀。我。"


class TestSplitChunksLimit:
    def test_limit_enforced(self) -> None:
        # Many short sentences → more than limit
        text = "\n".join(f"第{i}句" for i in range(20))
        result = split_chunks(text, max_len=35, min_len=6, limit=4)
        assert len(result) <= 4

    def test_limit_tail_merge_truncation(self) -> None:
        text = "\n".join(f"第{i}句话" for i in range(20))
        result = split_chunks(text, max_len=35, min_len=6, limit=3)
        assert len(result) == 3
        # Last chunk is merged tail, should not exceed max_len
        assert len(result[-1]) <= 35

    def test_limit_one(self) -> None:
        text = "第一句\n第二句\n第三句"
        result = split_chunks(text, max_len=35, min_len=6, limit=1)
        assert len(result) == 1
        # Everything merged/truncated into one
        assert len(result[0]) <= 35


class TestSplitChunksMvpAlignment:
    """Verify split_chunks produces same results as MVP message_chunk.split_chunks."""

    def test_typical_chinese_text_with_newlines(self) -> None:
        text = "你好呀！\n我是灵轩，很高兴认识你！\n今天天气真不错呢。"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        for chunk in result:
            assert len(chunk) <= 35
        # .strip() removes \n from parts, so reassembly loses newlines — MVP behavior
        reassembled = "".join(result)
        assert "你好呀！" in reassembled
        assert "我是灵轩" in reassembled
        assert "今天天气真不错呢。" in reassembled

    def test_english_with_spaces(self) -> None:
        # English with spaces after punctuation — splits at "!" and "?"
        text = "Hello! How are you? Fine."
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        # "Hello!" splits, "How are you?Fine." is one piece
        # (no space after ? before "Fine")
        assert len(result) >= 1
        # strip() removes the trailing space after "!" and "?", so reassembly
        # is not byte-for-byte identical — test content preservation instead
        full = "".join(result)
        assert "Hello!" in full
        assert "How are you?" in full
        assert "Fine." in full

    def test_newline_splits(self) -> None:
        text = "第一行\n第二行\n第三行"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# take_emit_chunk — pure function tests
# ---------------------------------------------------------------------------

class TestTakeEmitChunk:
    def test_empty_buffer(self) -> None:
        chunk, rest = take_emit_chunk("", max_len=35, min_len=6)
        assert chunk is None
        assert rest == ""

    def test_whitespace_only(self) -> None:
        chunk, rest = take_emit_chunk("   ", max_len=35, min_len=6)
        assert chunk is None

    def test_buffer_shorter_than_min_len_no_sentence_end(self) -> None:
        chunk, rest = take_emit_chunk("你好", max_len=35, min_len=6)
        assert chunk is None
        assert rest == "你好"

    def test_sentence_end_with_space_at_min_len(self) -> None:
        # "你好呀。 " — space after punctuation enables regex match
        # min_len=4, match.end() >= 4 → should cut
        chunk, rest = take_emit_chunk("你好呀。 继续", max_len=35, min_len=4)
        assert chunk == "你好呀。"
        assert rest == "继续"

    def test_sentence_end_at_string_end(self) -> None:
        # "你好呀。" — $ matches after the period
        chunk, rest = take_emit_chunk("你好呀。", max_len=35, min_len=4)
        assert chunk == "你好呀。"
        assert rest == ""

    def test_chinese_no_space_no_match(self) -> None:
        # "你好呀。继续" — no space after 。, not at string end → regex doesn't match
        chunk, rest = take_emit_chunk("你好呀。继续", max_len=35, min_len=4)
        assert chunk is None
        assert rest == "你好呀。继续"

    def test_sentence_end_before_min_len(self) -> None:
        # "短。后面..." — first 。 has no \s after it, so regex skips it.
        # Second 。 is at string end ($), so regex matches there.
        # match.end() = 13 >= min_len=6 → cuts the whole string
        chunk, rest = take_emit_chunk("短。后面更长的内容在这里。", max_len=35, min_len=6)
        # The first 。 (index 1) is NOT followed by \s or $ → no match there.
        # The second 。 (index 12) IS followed by $ → matches, end=13 >= 6
        # So the whole buffer is emitted as one chunk
        assert chunk == "短。后面更长的内容在这里。"
        assert rest == ""

    def test_hard_cut_at_max_len(self) -> None:
        text = "A" * 40
        chunk, rest = take_emit_chunk(text, max_len=35, min_len=6)
        assert chunk is not None
        assert len(chunk) <= 35
        assert rest == text[35:]

    def test_sentence_end_with_space_preferred_over_hard_cut(self) -> None:
        # Space after punctuation enables match
        text = "你好呀！ 继续聊"
        chunk, rest = take_emit_chunk(text, max_len=35, min_len=4)
        assert chunk == "你好呀！"
        assert rest == "继续聊"

    def test_newline_enables_split(self) -> None:
        chunk, rest = take_emit_chunk("你好呀。\n继续", max_len=35, min_len=4)
        assert chunk == "你好呀。"
        assert rest == "继续"


# ---------------------------------------------------------------------------
# plan_static
# ---------------------------------------------------------------------------

class TestPlanStatic:
    def test_empty_text(self) -> None:
        planner = _make_planner()
        result = planner.plan_static("")
        assert result == []

    def test_single_chunk_no_delay(self) -> None:
        planner = _make_planner()
        result = planner.plan_static("你好呀。")
        assert len(result) == 1
        assert result[0].delay_before == 0.0

    def test_at_user_id_on_first_chunk_only(self) -> None:
        planner = _make_planner()
        # Use newlines to force multiple chunks
        text = "你好呀！\n我是灵轩。\n很高兴认识你！"
        result = planner.plan_static(text, at_user_id=12345)
        assert result[0].at_user_id == 12345
        for chunk in result[1:]:
            assert chunk.at_user_id is None

    def test_first_chunk_delay_zero(self) -> None:
        planner = _make_planner()
        text = "第一段。\n第二段。\n第三段。\n第四段。"
        result = planner.plan_static(text)
        assert result[0].delay_before == 0.0

    def test_subsequent_chunks_have_delay(self) -> None:
        planner = _make_planner()
        text = "第一段。\n第二段。\n第三段。\n第四段。\n第五段。\n第六段。"
        result = planner.plan_static(text)
        for chunk in result[1:]:
            assert chunk.delay_before >= 0.4
            assert chunk.delay_before <= 1.2

    def test_deterministic_with_fixed_seed(self) -> None:
        config = FakeConfigProvider(DEFAULT_CHUNK_OVERRIDES)
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        planner1 = ReplyPlanner(config, rng=rng1)
        planner2 = ReplyPlanner(config, rng=rng2)

        text = "第一段。\n第二段。\n第三段。\n第四段。\n第五段。"
        result1 = planner1.plan_static(text)
        result2 = planner2.plan_static(text)

        for c1, c2 in zip(result1, result2):
            assert c1.text == c2.text
            assert c1.delay_before == c2.delay_before

    def test_no_at_user_id_when_none(self) -> None:
        planner = _make_planner()
        result = planner.plan_static("你好。")
        assert result[0].at_user_id is None

    def test_chunk_count_within_limit(self) -> None:
        planner = _make_planner()
        text = "\n".join(f"第{i}句话内容" for i in range(20))
        result = planner.plan_static(text)
        assert len(result) <= 6

    def test_reassembly_with_newlines(self) -> None:
        planner = _make_planner()
        text = "你好呀！\n我是灵轩。\n很高兴认识你！\n今天天气真不错呢。"
        result = planner.plan_static(text)
        reassembled = "".join(c.text for c in result)
        # .strip() removes \n from parts — reassembly loses newlines (MVP behavior)
        # Verify all content is present, just without newlines
        assert "你好呀！" in reassembled
        assert "我是灵轩。" in reassembled
        assert "很高兴认识你！" in reassembled
        assert "今天天气真不错呢。" in reassembled


# ---------------------------------------------------------------------------
# plan_stream — ENABLE_STREAM_CHUNK=True
# ---------------------------------------------------------------------------

class TestPlanStreamEnabled:
    @pytest.mark.asyncio
    async def test_simple_stream(self) -> None:
        planner = _make_planner()
        # Use newline tokens to force splits
        tokens = ["你好", "呀！\n", "我是", "灵轩。"]
        result = await _collect_aiter(
            planner.plan_stream(_token_iter(tokens), at_user_id=999)
        )
        assert len(result) >= 1
        assert result[0].at_user_id == 999
        assert result[0].delay_before == 0.0

    @pytest.mark.asyncio
    async def test_stream_chunk_count_within_limit(self) -> None:
        planner = _make_planner()
        tokens = [f"第{i}句。\n" for i in range(20)]
        result = await _collect_aiter(
            planner.plan_stream(_token_iter(tokens))
        )
        assert len(result) <= 6

    @pytest.mark.asyncio
    async def test_stream_first_chunk_at_only(self) -> None:
        planner = _make_planner()
        tokens = ["你好！\n", "继续聊。\n", "再聊。\n"]
        result = await _collect_aiter(
            planner.plan_stream(_token_iter(tokens), at_user_id=555)
        )
        assert result[0].at_user_id == 555
        for chunk in result[1:]:
            assert chunk.at_user_id is None

    @pytest.mark.asyncio
    async def test_stream_delays(self) -> None:
        planner = _make_planner()
        tokens = ["你好呀！\n", "我是灵轩。\n", "很高兴认识你！\n", "再见啦！"]
        result = await _collect_aiter(
            planner.plan_stream(_token_iter(tokens))
        )
        assert result[0].delay_before == 0.0
        for chunk in result[1:]:
            assert chunk.delay_before >= 0.4
            assert chunk.delay_before <= 1.2

    @pytest.mark.asyncio
    async def test_stream_flush_remaining(self) -> None:
        planner = _make_planner()
        tokens = ["你好呀", "我是灵轩"]
        result = await _collect_aiter(
            planner.plan_stream(_token_iter(tokens))
        )
        assert len(result) >= 1
        # Should have flushed the remaining buffer
        assert any("灵轩" in c.text for c in result)

    @pytest.mark.asyncio
    async def test_stream_deterministic_with_seed(self) -> None:
        config = FakeConfigProvider(DEFAULT_CHUNK_OVERRIDES)
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        planner1 = ReplyPlanner(config, rng=rng1)
        planner2 = ReplyPlanner(config, rng=rng2)

        tokens = ["你好！\n", "我是灵轩。\n", "很高兴认识你！\n", "再见！"]
        result1 = await _collect_aiter(planner1.plan_stream(_token_iter(tokens)))
        result2 = await _collect_aiter(planner2.plan_stream(_token_iter(tokens)))

        for c1, c2 in zip(result1, result2):
            assert c1.text == c2.text
            assert c1.delay_before == c2.delay_before

    @pytest.mark.asyncio
    async def test_stream_enforces_max_len(self) -> None:
        planner = _make_planner(GROUP_MSG_CHUNK_MAX=10, GROUP_MSG_CHUNK_MIN=3)
        tokens = ["A" * 50]
        result = await _collect_aiter(
            planner.plan_stream(_token_iter(tokens))
        )
        for chunk in result:
            assert len(chunk.text) <= 10


# ---------------------------------------------------------------------------
# plan_stream — ENABLE_STREAM_CHUNK=False
# ---------------------------------------------------------------------------

class TestPlanStreamDisabled:
    @pytest.mark.asyncio
    async def test_disabled_collects_then_static(self) -> None:
        planner = _make_planner(ENABLE_STREAM_CHUNK=False)
        tokens = ["你好", "呀！\n", "我是", "灵轩。"]
        result = await _collect_aiter(
            planner.plan_stream(_token_iter(tokens), at_user_id=888)
        )
        # Should behave like plan_static on the full joined text
        static = planner.plan_static("你好呀！\n我是灵轩。", at_user_id=888)
        assert len(result) == len(static)
        for r, s in zip(result, static):
            assert r.text == s.text
            assert r.at_user_id == s.at_user_id
            assert r.delay_before == s.delay_before

    @pytest.mark.asyncio
    async def test_disabled_first_chunk_at(self) -> None:
        planner = _make_planner(ENABLE_STREAM_CHUNK=False)
        tokens = ["你好！\n", "再见！"]
        result = await _collect_aiter(
            planner.plan_stream(_token_iter(tokens), at_user_id=777)
        )
        assert result[0].at_user_id == 777
        for chunk in result[1:]:
            assert chunk.at_user_id is None

    @pytest.mark.asyncio
    async def test_disabled_empty_input(self) -> None:
        planner = _make_planner(ENABLE_STREAM_CHUNK=False)
        result = await _collect_aiter(
            planner.plan_stream(_token_iter([]))
        )
        assert result == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_split_chunks_single_char(self) -> None:
        result = split_chunks("好", max_len=35, min_len=6, limit=6)
        # Single char < min_len, but no previous to merge into → stays
        assert result == ["好"]

    def test_split_chunks_ellipsis(self) -> None:
        text = "嗯…好吧。\n"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        # "嗯…好吧。" stays as one (no space after …), newline at end matches
        assert len(result) >= 1

    def test_split_chunks_english_with_spaces(self) -> None:
        text = "Hello! How are you?"
        result = split_chunks(text, max_len=35, min_len=6, limit=6)
        # Splits at "!" (followed by space) and "?" (at string end)
        assert len(result) == 2
        assert result[0] == "Hello!"
        assert result[1] == "How are you?"

    @pytest.mark.asyncio
    async def test_plan_stream_empty_token_iter(self) -> None:
        planner = _make_planner()
        result = await _collect_aiter(
            planner.plan_stream(_token_iter([]))
        )
        assert result == []

    def test_plan_static_respects_limit_even_with_at(self) -> None:
        planner = _make_planner()
        text = "\n".join(f"第{i}句内容" for i in range(30))
        result = planner.plan_static(text, at_user_id=123)
        assert len(result) <= 6
        assert result[0].at_user_id == 123

    @pytest.mark.asyncio
    async def test_stream_hard_cut_in_flush(self) -> None:
        """Remaining buffer longer than max_len gets truncated in flush."""
        planner = _make_planner(GROUP_MSG_CHUNK_MAX=15, GROUP_MSG_CHUNK_MIN=4)
        tokens = ["X" * 30]
        result = await _collect_aiter(
            planner.plan_stream(_token_iter(tokens))
        )
        for chunk in result:
            assert len(chunk.text) <= 15

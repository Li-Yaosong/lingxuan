from __future__ import annotations

import re

from lingxuan.group_observer import ObservationEntry, remember_user_nickname
from lingxuan.memory import merge_entity
from lingxuan.user_memory import apply_rule_extraction, index_name, sync_entity_to_graph

_INTRO_NAME = re.compile(
    r"(?:这位就是|这就是|他(?:就)?是|她(?:就)?是|叫)([^，。！？\s]{1,12})"
)


def learn_entities_from_entry(
    session_id: str,
    group_id: int,
    entry: ObservationEntry,
) -> None:
    if entry.is_bot or not entry.text.strip():
        return

    sync_entity_to_graph(entry.nickname, entry.user_id, session_id)

    text = entry.text
    for uid in entry.at_user_ids:
        remember_user_nickname(group_id, uid, str(uid))
        if "小堞宝" in text:
            merge_entity(session_id, "小堞宝", uid)
            index_name("小堞宝", uid)
        match = _INTRO_NAME.search(text)
        if match:
            name = match.group(1).strip().strip("的")
            if name and len(name) <= 12:
                merge_entity(session_id, name, uid)

    if "就是" in text and not entry.at_user_ids:
        match = _INTRO_NAME.search(text)
        if match:
            name = match.group(1).strip()
            if name and len(name) <= 12:
                merge_entity(session_id, name, entry.user_id)

    apply_rule_extraction(
        entry.user_id,
        entry.text,
        nickname=entry.nickname,
        group_id=group_id,
        at_user_ids=entry.at_user_ids,
        session_id=session_id,
    )

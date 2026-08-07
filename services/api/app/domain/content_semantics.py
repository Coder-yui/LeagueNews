from __future__ import annotations

import re
from typing import Final


GAMEPLAY_CHANGE_SUBTOPICS: Final = frozenset(
    {
        "champion_release",
        "champion_update",
        "item_rune_system",
        "game_mode_release",
        "game_mode_update",
    }
)
OFFICIAL_ONLY_UPDATE_SUBTOPICS: Final = GAMEPLAY_CHANGE_SUBTOPICS | {
    "patch_notes",
    "patch_preview",
    "hotfix",
    "tft_patch",
    "skin_release",
    "tft_cosmetic",
}

_HOTFIX_SIGNAL = re.compile(
    r"不停机.{0,8}(?:更新|维护)|(?:无需|不用)停机.{0,8}(?:更新|维护)|"
    r"热(?:更新|修复)|hotfix|micro\s*patch|server[-\s]?side\s+update",
    re.IGNORECASE,
)
_TEST_ENVIRONMENT_SIGNAL = re.compile(
    r"测试服|体验服|\bpbe\b|public\s+beta\s+environment|数据挖掘|datamin(?:e|ed|ing)",
    re.IGNORECASE,
)
_CATALOG_SUBJECT_SIGNAL = re.compile(
    r"礼包|捆绑包|商城|商店|售价|价格|获取方式|bundle|shop|store",
    re.IGNORECASE,
)
_CATALOG_ASSET_SIGNAL = re.compile(
    r"封面|图标|物料|目录|一览|预览|cover|icon|asset|catalog|preview",
    re.IGNORECASE,
)
_INTERACTION_PROMPT_SIGNAL = re.compile(
    r"(?:你|大家|各位).{0,8}(?:记得|觉得|认为|喜欢|会选|最想|怎么看|怎么想|有哪些).{0,10}[?？]"
    r"|(?:评论区|留言区).{0,8}(?:聊聊|说说|告诉|分享)"
    r"|(?:来|一起)(?:聊聊|说说|回忆).{0,12}[?？]?"
    r"|what\s+do\s+you\s+think|tell\s+us|share\s+your",
    re.IGNORECASE,
)
_CONCRETE_UPDATE_DETAIL_SIGNAL = re.compile(
    r"(?<!\d)\d{1,2}\.\d{1,2}(?!\d)|20\d{2}[年\-/]\d{1,2}[月\-/]\d{1,2}日?"
    r"|\d{1,2}月\d{1,2}日|\d{1,2}:\d{2}"
    r"|新增|移除|调整|重做|修复|削弱|增强|改动|数值|技能|装备|强化符文|开放时间"
    r"|adds?|removes?|adjusts?|reworks?|fixes?|buffs?|nerfs?",
    re.IGNORECASE,
)


def has_hotfix_signal(text: str) -> bool:
    return _HOTFIX_SIGNAL.search(text) is not None


def has_test_environment_signal(text: str) -> bool:
    return _TEST_ENVIRONMENT_SIGNAL.search(text) is not None


def is_catalog_asset_preview(text: str) -> bool:
    return bool(_CATALOG_SUBJECT_SIGNAL.search(text) and _CATALOG_ASSET_SIGNAL.search(text))


def is_interaction_post_without_update_details(text: str) -> bool:
    """Identify engagement-first posts that only mention an update as context."""
    return bool(
        _INTERACTION_PROMPT_SIGNAL.search(text)
        and not _CONCRETE_UPDATE_DETAIL_SIGNAL.search(text)
    )

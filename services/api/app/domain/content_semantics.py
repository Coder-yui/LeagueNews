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


def has_hotfix_signal(text: str) -> bool:
    return _HOTFIX_SIGNAL.search(text) is not None


def has_test_environment_signal(text: str) -> bool:
    return _TEST_ENVIRONMENT_SIGNAL.search(text) is not None


def is_catalog_asset_preview(text: str) -> bool:
    return bool(_CATALOG_SUBJECT_SIGNAL.search(text) and _CATALOG_ASSET_SIGNAL.search(text))

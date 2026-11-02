"""法規の列(自家用か・許可の要否・許可の期限・次回点検)を埋めるための部品。

住所→自治体(municipality)、条例からの規則抽出(rules_extract)、判定(judge)、CLI(cli)。
条例原文の写しは data/ordinances/(MANIFEST.tsv 付き)。詳細は docs/LEGAL.md。
"""
from .municipality import AddressResolver, Municipality, MunicipalityResolver, NullResolver, ordinance_key  # noqa: F401

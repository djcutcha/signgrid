"""法規の列(自家用か・許可の要否・許可の期限・次回点検)を埋めるための部品。

この段階はインターフェースだけ。条例原文は
~/Projects/fkogei-estimate-data/hackathon_inputs/ordinances/(MANIFEST.tsv 付き)にある。
"""
from .municipality import Municipality, MunicipalityResolver, NullResolver  # noqa: F401

"""今日の案件(現場名・住所・予定日・看板数)。

デモは data/sample/jobs.json(架空の案件)。実運用は SignJOBZ の点検指示書から同じ形で渡す(後日接続)。
形: {"jobs": [{"id": "J-001", "site_name": "...", "address": "...", "date": "YYYY-MM-DD", "signs": 4}]}
見本は "date" の代わりに "day_offset"(今日からの日数。-1 = 昨日)で書き、読み込む時に今日(日本時間)から日付を決める
(見本の案件が日付の経過で現場の候補から消えないように)。両方あれば "date" を使う。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "sample" / "jobs.json"
JST = timezone(timedelta(hours=9))
MAX_CHOICES = 4  # クイックリプライは「その他」と合わせて 5 個まで


@dataclass(frozen=True)
class Job:
    id: str
    site_name: str
    address: str
    date: date
    signs: int | None = None


def load_jobs(path: str | Path | None = None, today: date | None = None) -> list[Job]:
    """ファイルが無い・壊れている時は空(現場の選択は「その他」だけになる)。day_offset は today(既定は日本時間の今日)から数える。"""
    p = Path(path) if path else DEFAULT_PATH
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        base = today or datetime.now(JST).date()
        out = []
        for j in doc["jobs"]:
            if "date" in j:
                d = datetime.strptime(j["date"], "%Y-%m-%d").date()
            elif isinstance(j["day_offset"], int) and not isinstance(j["day_offset"], bool):
                d = base + timedelta(days=j["day_offset"])
            else:
                raise ValueError(f"day_offset は整数: {j['day_offset']!r}")
            out.append(Job(str(j["id"]), str(j["site_name"]), str(j["address"]), d, j.get("signs")))
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return []


def jobs_near(jobs: list[Job], today: date, days: int = 1, limit: int = MAX_CHOICES) -> list[Job]:
    """予定日が today ±days 日の案件。今日に近い順、同じなら読み込み順。"""
    near = [j for j in jobs if abs((j.date - today).days) <= days]
    near.sort(key=lambda j: abs((j.date - today).days))
    return near[:limit]


def find_job(jobs: list[Job], job_id: str) -> Job | None:
    return next((j for j in jobs if j.id == job_id), None)

"""今日の案件(現場名・住所・予定日・看板数)。

デモは data/sample/jobs.json(架空の案件)。実運用は SignJOBZ の点検指示書から同じ形で渡す(後日接続)。
形: {"jobs": [{"id": "J-001", "site_name": "...", "address": "...", "date": "YYYY-MM-DD", "signs": 4}]}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "sample" / "jobs.json"
MAX_CHOICES = 4  # クイックリプライは「その他」と合わせて 5 個まで


@dataclass(frozen=True)
class Job:
    id: str
    site_name: str
    address: str
    date: date
    signs: int | None = None


def load_jobs(path: str | Path | None = None) -> list[Job]:
    """ファイルが無い・壊れている時は空(現場の選択は「その他」だけになる)。"""
    p = Path(path) if path else DEFAULT_PATH
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        out = []
        for j in doc["jobs"]:
            out.append(Job(str(j["id"]), str(j["site_name"]), str(j["address"]),
                           datetime.strptime(j["date"], "%Y-%m-%d").date(), j.get("signs")))
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

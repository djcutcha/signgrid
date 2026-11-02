"""LINE 入口 v2: 返信ログ、現場の選択(クイックリプライ/postback)、位置の付け替え、リッチメニュー、住所検索。通信はすべてモック。"""
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from kanban_karte.intake.line import client as line_client
from kanban_karte.intake.line import reply as reply_text
from kanban_karte.intake.line.address import AddressSearch
from kanban_karte.intake.line.jobs import Job, jobs_near
from kanban_karte.intake.line.webhook import LineApp
from kanban_karte.ledger import Ledger
from tests.test_line import SECRET, T0, USER, Base, img_event, jpeg


class _Resp(io.BytesIO):
    def __init__(self, body=b"{}", status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ReplyLogTest(Base):
    def test_ok_logged_with_bundle(self):
        self.client.contents["m1"] = jpeg()
        with self.assertLogs("kanban_karte.line", "INFO") as cm:
            self.post(self.app(), img_event("m1"))
        self.assertTrue(any("reply ok 1通 bundle=#1" in m for m in cm.output), cm.output)

    def test_failure_logged(self):
        self.client.contents["m1"] = jpeg()

        def boom(token, text, **kw):
            raise IOError("LINE 側のエラー")
        self.client.reply = boom
        with self.assertLogs("kanban_karte.line", "ERROR") as cm:
            self.post(self.app(), img_event("m1"))
        self.assertTrue(any("reply failed bundle=#1" in m and "LINE 側のエラー" in m for m in cm.output))


class HttpClientLogTest(unittest.TestCase):
    def test_success_logs_status(self):
        c = line_client.HttpLineClient("tok")
        with mock.patch("urllib.request.urlopen", return_value=_Resp()) as op, \
                self.assertLogs("kanban_karte.line", "INFO") as cm:
            c.reply("rt", "こんにちは")
        sent = json.loads(op.call_args[0][0].data)
        self.assertEqual(sent["messages"], [{"type": "text", "text": "こんにちは"}])
        self.assertTrue(any("reply -> HTTP 200" in m for m in cm.output))

    def test_http_error_logs_status_and_body(self):
        c = line_client.HttpLineClient("tok")
        err = urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b'{"message":"Invalid reply token"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err), \
                self.assertLogs("kanban_karte.line", "ERROR") as cm:
            with self.assertRaises(urllib.error.HTTPError):
                c.reply("rt", "x")
        self.assertTrue(any("HTTP 400" in m and "Invalid reply token" in m for m in cm.output))


JOBS = [
    Job("J-1", "テスト一号店", "東京都豊島区東池袋1-1-1", date(2026, 11, 3), 4),   # 今日(T0 = 11/3)
    Job("J-2", "テスト二号店", "埼玉県越谷市南越谷1-1-1", date(2026, 11, 4), 2),   # 翌日
    Job("J-3", "テスト三号店", "東京都新宿区西新宿2-8-1", date(2026, 11, 2), 6),   # 前日
    Job("J-4", "テスト四号店", "東京都港区芝公園4-2-8", date(2026, 11, 5), 1),     # 2日後 = 対象外
]


def postback(data, user=USER, token="pb-tok"):
    return {"type": "postback", "replyToken": token, "source": {"type": "user", "userId": user},
            "postback": {"data": data}}


class FakeSearch:
    def __init__(self):
        self.asked = []

    def lookup(self, address):
        self.asked.append(address)
        return (35.7295, 139.7109)


class SiteChoiceTest(Base):
    def app(self):
        self.search = FakeSearch()
        return LineApp(SECRET, self.client, lambda: Ledger(self.db), self.inbox, now=lambda: self.clock[0],
                       jobs=lambda: JOBS, address_search=self.search)

    def test_quick_replies_for_todays_jobs_plus_other(self):
        self.client.contents["m1"] = jpeg()
        self.post(self.app(), img_event("m1"))
        items = self.client.extras[0]["quick_replies"]
        self.assertEqual([i["action"]["label"] for i in items],
                         ["テスト一号店", "テスト二号店", "テスト三号店", "その他(現在地を送る)"])
        self.assertTrue(all(len(i["action"]["label"]) <= 20 for i in items))
        self.assertEqual(self.client.replies[0][1].splitlines()[-1], "今日の現場はどれですか")
        self.assertIn("job=J-1", items[0]["action"]["data"])

    def test_max_four_jobs(self):
        many = [Job(f"J-{i}", f"店{i}", "東京都豊島区東池袋1-1-1", date(2026, 11, 3)) for i in range(7)]
        items = reply_text.site_choices(jobs_near(many, date(2026, 11, 3)), 1)
        self.assertEqual(len(items), 5)  # 4 + その他

    def test_long_label_clipped(self):
        j = Job("J", "あ" * 30, "x", date(2026, 11, 3))
        self.assertEqual(len(reply_text.site_choices([j], None)[0]["action"]["label"]), 20)

    def test_no_question_when_position_known(self):
        self.client.contents["m1"] = jpeg(gps=(35.6895, 139.6917))
        self.post(self.app(), img_event("m1"))
        self.assertIsNone(self.client.extras[0]["quick_replies"])
        self.assertNotIn("今日の現場", self.client.replies[0][1])

    def test_postback_binds_site_and_coordinates(self):
        self.client.contents["m1"] = jpeg()
        app = self.app()
        self.post(app, img_event("m1"))
        data = self.client.extras[0]["quick_replies"][0]["action"]["data"]
        code, res = self.post(app, postback(data))
        self.assertEqual(code, 200)
        self.assertEqual(res.replies, [("pb-tok", "テスト一号店で受け付けました")])
        self.assertEqual(self.search.asked, ["東京都豊島区東池袋1-1-1"])
        with Ledger(self.db) as led:
            b = led.get_bundle(1)
            self.assertEqual(b["site_id"], "job-J-1")
            s = led.get_site("job-J-1")
            self.assertEqual((s.name, s.address, s.lat, s.lng), ("テスト一号店", "東京都豊島区東池袋1-1-1", 35.7295, 139.7109))
            self.assertEqual(s.municipality, "豊島区")
        # 続けて写真を送ると、もう現場は尋ねない
        self.client.contents["m2"] = jpeg("blue")
        self.post(app, img_event("m2"))
        self.assertIsNone(self.client.extras[-1]["quick_replies"])

    def test_postback_without_bundle_or_unknown_job(self):
        app = self.app()
        _, res = self.post(app, postback("act=site&job=J-1"))
        self.assertEqual(res.replies[0][1], reply_text.no_bundle())
        self.client.contents["m1"] = jpeg()
        self.post(app, img_event("m1"))
        _, res = self.post(app, postback("act=site&job=NOPE&b=1"))
        self.assertEqual(res.replies[0][1], reply_text.job_not_found())

    def test_postback_for_someone_elses_bundle_is_refused(self):
        self.client.contents["m1"] = jpeg()
        app = self.app()
        self.post(app, img_event("m1"))
        _, res = self.post(app, postback("act=site&job=J-1&b=1", user="U" + "b" * 32))
        self.assertEqual(res.replies[0][1], reply_text.no_bundle())
        with Ledger(self.db) as led:
            self.assertIsNone(led.get_bundle(1)["site_id"])

    def test_other_gives_location_button(self):
        self.client.contents["m1"] = jpeg()
        app = self.app()
        self.post(app, img_event("m1"))
        _, res = self.post(app, postback("act=other&b=1"))
        self.assertEqual(self.client.extras[-1]["button"], ("現在地を送る", "https://line.me/R/nv/location"))
        self.assertEqual(res.replies[0][1], reply_text.other_prompt())

    def test_today_lists_jobs(self):
        app = self.app()
        _, res = self.post(app, postback("act=today"))
        self.assertEqual(res.replies[0][1], "今日の案件\n・テスト一号店\n・テスト二号店\n・テスト三号店")


def loc_event(mid, lat=35.7, lng=139.7, user=USER):
    return {"type": "message", "replyToken": f"tok-{mid}", "source": {"type": "user", "userId": user},
            "message": {"type": "location", "id": mid, "latitude": lat, "longitude": lng, "address": "東京都豊島区"}}


class LocationWindowTest(Base):
    def app(self):
        return LineApp(SECRET, self.client, lambda: Ledger(self.db), self.inbox, now=lambda: self.clock[0],
                       jobs=lambda: [])

    def at(self, minutes):
        self.clock[0] = T0 + timedelta(minutes=minutes)

    def site_of(self, bundle_id):
        with Ledger(self.db) as led:
            return led.get_bundle(bundle_id)["site_id"]

    def test_location_18min_after_photos_joins_the_photo_bundle(self):
        app = self.app()
        self.client.contents.update(m1=jpeg(), m2=jpeg("blue"))
        self.post(app, img_event("m1"), img_event("m2"))
        self.at(18)  # 10分窓は閉じている
        self.post(app, loc_event("L1"))
        bs = self.bundles()
        self.assertEqual(len(bs), 1)
        self.assertEqual(self.site_of(1), "line-1")
        with Ledger(self.db) as led:
            self.assertEqual(led.get_site("line-1").lat, 35.7)

    def test_location_after_61min_makes_a_new_bundle(self):
        app = self.app()
        self.client.contents["m1"] = jpeg()
        self.post(app, img_event("m1"))
        self.at(61)
        self.post(app, loc_event("L1"))
        self.assertEqual(len(self.bundles()), 2)
        self.assertIsNone(self.site_of(1))
        self.assertEqual(self.site_of(2), "line-2")

    def test_picks_latest_bundle_without_a_position(self):
        app = self.app()
        self.client.contents.update(m1=jpeg(), m2=jpeg("blue"), m3=jpeg("red"))
        self.post(app, img_event("m1"))                 # 束1(位置なし)
        self.at(15)
        self.post(app, img_event("m2"))                 # 束2(位置なし、最新)
        self.at(40)
        self.post(app, loc_event("L1"))
        self.assertEqual(len(self.bundles()), 2)
        self.assertIsNone(self.site_of(1))
        self.assertEqual(self.site_of(2), "line-2")
        self.at(45)
        self.post(app, loc_event("L2", 35.8))           # 次の位置は残った束1へ
        self.assertEqual(self.site_of(1), "line-1")
        self.assertEqual(len(self.bundles()), 2)

    def test_skips_bundle_that_already_has_a_site(self):
        app = self.app()
        self.client.contents.update(m1=jpeg(gps=(35.6, 139.6)), m2=jpeg("blue"))
        self.post(app, img_event("m1"))                 # EXIF で位置あり
        self.at(15)
        self.post(app, img_event("m2"))                 # 位置なし
        self.at(20)
        self.post(app, loc_event("L1"))
        self.assertEqual(self.site_of(2), "line-2")
        self.assertEqual(len(self.bundles()), 2)

    def test_redelivered_location_is_ignored(self):
        app = self.app()
        self.client.contents["m1"] = jpeg()
        self.post(app, img_event("m1"))
        self.at(18)
        self.post(app, loc_event("L1"))
        self.at(30)
        _, res = self.post(app, loc_event("L1"))
        self.assertEqual(res.replies, [])
        self.assertEqual(len(self.bundles()), 1)

    def test_photos_still_group_by_ten_minutes(self):
        app = self.app()
        self.client.contents.update(m1=jpeg(), m2=jpeg("blue"), m3=jpeg("red"))
        self.post(app, img_event("m1"))
        self.at(9)
        self.post(app, img_event("m2"))
        self.at(20)  # 最後の写真(9分)から11分 = 窓の外
        self.post(app, img_event("m3"))
        self.assertEqual([b["pages"] for b in self.bundles()], [2, 1])


class AddressSearchTest(unittest.TestCase):
    def reply_for(self, lng=139.71, lat=35.73):
        return [{"geometry": {"type": "Point", "coordinates": [lng, lat]}, "properties": {"title": "x"}}]

    def make(self, fetch, path=None):
        self.now = [100.0]
        self.slept = []
        return AddressSearch(path, fetch, sleep=lambda s: (self.slept.append(s), self.now.__setitem__(0, self.now[0] + s)),
                             clock=lambda: self.now[0])

    def test_lng_lat_order_and_cache(self):
        calls = []
        s = self.make(lambda a: calls.append(a) or self.reply_for())
        self.assertEqual(s.lookup("東京都豊島区東池袋1-1-1"), (35.73, 139.71))
        self.assertEqual(s.lookup("東京都豊島区東池袋1-1-1"), (35.73, 139.71))
        self.assertEqual(len(calls), 1)

    def test_cache_persists_in_file_and_not_found_is_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            calls = []
            s = self.make(lambda a: calls.append(a) or ([] if a == "無い" else self.reply_for()), path)
            self.assertIsNone(s.lookup("無い"))
            s.lookup("ある")
            s2 = self.make(lambda a: calls.append(a) or [], path)
            self.assertIsNone(s2.lookup("無い"))
            self.assertEqual(s2.lookup("ある"), (35.73, 139.71))
            self.assertEqual(len(calls), 2)

    def test_min_interval_between_network_calls(self):
        s = self.make(lambda a: self.reply_for())
        s.lookup("a")
        s.lookup("b")
        s.lookup("a")  # キャッシュ: 待たない
        self.assertEqual(len(self.slept), 1)
        self.assertAlmostEqual(self.slept[0], 1.0)

    def test_network_error_not_cached(self):
        n = [0]

        def fetch(a):
            n[0] += 1
            if n[0] == 1:
                raise urllib.error.URLError("down")
            return self.reply_for()
        s = self.make(fetch)
        self.assertIsNone(s.lookup("a"))
        self.assertEqual(s.lookup("a"), (35.73, 139.71))


if __name__ == "__main__":
    unittest.main()

import io
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from kanban_karte.intake.line import reply as reply_text
from kanban_karte.intake.line.cli import main as cli_main
from kanban_karte.intake.line.exif import gps_from_bytes, image_format
from kanban_karte.intake.line.session import JST, SessionStore, safe_name
from kanban_karte.intake.line.simulate import DirContentClient, build_deliveries, post
from kanban_karte.intake.line.webhook import LineApp, make_server, sign, verify_signature
from kanban_karte.ledger import Ledger

SECRET = "test-secret"
USER = "U" + "a" * 32
T0 = datetime(2026, 11, 3, 10, 0, 0, tzinfo=JST)


def jpeg(color="gray", gps=None) -> bytes:
    im = Image.new("RGB", (64, 48), color)
    buf = io.BytesIO()
    if gps is None:
        im.save(buf, "JPEG")
    else:
        ex = Image.Exif()
        g = ex.get_ifd(0x8825)
        lat, lng = gps
        g[1], g[2] = ("N" if lat >= 0 else "S"), tuple(_dms(abs(lat)))
        g[3], g[4] = ("E" if lng >= 0 else "W"), tuple(_dms(abs(lng)))
        im.save(buf, "JPEG", exif=ex)
    return buf.getvalue()


def _dms(v):
    d = int(v)
    m = int((v - d) * 60)
    return float(d), float(m), (v - d - m / 60) * 3600


class FakeClient:
    def __init__(self, contents=None):
        self.contents = contents or {}
        self.replies = []
        self.extras = []
        self.fetched = []

    def get_content(self, mid):
        self.fetched.append(mid)
        if mid not in self.contents:
            raise IOError("取得できない")
        return self.contents[mid]

    def reply(self, token, text, quick_replies=None, button=None, more=None):
        self.replies.append((token, text))
        self.extras.append({"quick_replies": quick_replies, "button": button, "more": more})


def img_event(mid, user=USER, token=None):
    return {"type": "message", "replyToken": token or f"tok-{mid}", "source": {"type": "user", "userId": user},
            "message": {"type": "image", "id": mid}}


def body_of(*events) -> bytes:
    return json.dumps({"destination": "U", "events": list(events)}, ensure_ascii=False).encode()


class SignatureTest(unittest.TestCase):
    def test_known_value(self):
        # base64(HMAC-SHA256("test-secret", "{}")) を独立に計算した値
        import base64, hashlib, hmac
        want = base64.b64encode(hmac.new(b"test-secret", b"{}", hashlib.sha256).digest()).decode()
        self.assertEqual(sign(SECRET, b"{}"), want)

    def test_verify(self):
        body = b'{"events":[]}'
        good = sign(SECRET, body)
        self.assertTrue(verify_signature(SECRET, body, good))
        self.assertFalse(verify_signature(SECRET, body, sign("other", body)))
        self.assertFalse(verify_signature(SECRET, body + b" ", good))  # 本文を1文字変えると通らない
        self.assertFalse(verify_signature(SECRET, body, None))
        self.assertFalse(verify_signature(SECRET, body, ""))
        self.assertFalse(verify_signature("", body, sign("", body)))  # 秘密が空なら常に拒否


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "ledger.sqlite"
        self.inbox = self.root / "line_inbox"
        self.clock = [T0]
        self.client = FakeClient()

    def tearDown(self):
        self.tmp.cleanup()

    def app(self):
        return LineApp(SECRET, self.client, lambda: Ledger(self.db), self.inbox, now=lambda: self.clock[0])

    def post(self, app, *events, signature=None):
        body = body_of(*events)
        return app.handle(body, signature or sign(SECRET, body))

    def bundles(self):
        with Ledger(self.db) as led:
            return [dict(b) for b in led.list_bundles()]


class ExifTest(unittest.TestCase):
    def test_gps(self):
        got = gps_from_bytes(jpeg(gps=(35.6895, 139.6917)))
        self.assertAlmostEqual(got[0], 35.6895, places=3)
        self.assertAlmostEqual(got[1], 139.6917, places=3)

    def test_south_west_negative(self):
        got = gps_from_bytes(jpeg(gps=(-33.8688, -70.5)))
        self.assertLess(got[0], 0)
        self.assertLess(got[1], 0)

    def test_none(self):
        self.assertIsNone(gps_from_bytes(jpeg()))
        self.assertIsNone(gps_from_bytes(b"not an image"))
        self.assertIsNone(gps_from_bytes(jpeg(gps=(0.0, 0.0))))

    def test_format(self):
        self.assertEqual(image_format(jpeg()), "JPEG")
        self.assertIsNone(image_format(b"xx"))


class WebhookTest(Base):
    def test_bad_signature_rejected_and_nothing_saved(self):
        app = self.app()
        self.client.contents["m1"] = jpeg()
        code, res = self.post(app, img_event("m1"), signature="AAAA")
        self.assertEqual((code, res), (401, None))
        self.assertEqual(self.client.fetched, [])
        self.assertFalse(self.inbox.exists())
        self.assertEqual(self.bundles(), [])

    def test_bad_json_is_400(self):
        body = b"not json"
        code, _ = self.app().handle(body, sign(SECRET, body))
        self.assertEqual(code, 400)

    def test_image_saved_under_user_and_date(self):
        app = self.app()
        self.client.contents["m1"] = jpeg()
        code, res = self.post(app, img_event("m1"))
        self.assertEqual(code, 200)
        p = self.inbox / USER / "2026-11-03" / "m1.jpg"
        self.assertTrue(p.exists())
        self.assertEqual(res.saved, [p.resolve()])
        self.assertEqual(p.read_bytes(), self.client.contents["m1"])

    def test_non_image_content_is_a_failure_not_a_crash(self):
        app = self.app()
        self.client.contents["m1"] = b"<html>error</html>"
        code, res = self.post(app, img_event("m1"))
        self.assertEqual(code, 200)
        self.assertEqual(len(res.errors), 1)
        self.assertEqual(res.replies[0][1], "1 枚は取得できませんでした。もう一度送ってください")
        self.assertEqual(self.bundles(), [])

    def test_fetch_failure_does_not_block_other_images(self):
        app = self.app()
        self.client.contents["m1"] = jpeg()
        code, res = self.post(app, img_event("m1"), img_event("m2"))
        self.assertEqual(len(res.saved), 1)
        self.assertEqual(len(res.replies), 1)
        self.assertEqual(res.replies[0][1],
                         "受け取りました。写真 1 枚。看板カルテの下書きを作っています。1 枚は取得できませんでした。もう一度送ってください\n" + reply_text.ask_site())

    def test_ignored_events(self):
        app = self.app()
        ev = {"type": "unfollow", "source": {"type": "user", "userId": USER}}
        sticker = {"type": "message", "replyToken": "t", "source": {"userId": USER},
                   "message": {"type": "sticker", "id": "s1"}}
        code, res = self.post(app, ev, sticker)
        self.assertEqual(code, 200)
        self.assertEqual(res.replies, [])
        self.assertEqual(self.bundles(), [])

    def test_follow_gets_one_guide_reply(self):
        app = self.app()
        code, res = self.post(app, {"type": "follow", "replyToken": "f1", "source": {"userId": USER}})
        self.assertEqual(res.replies, [("f1", reply_text.follow())])
        self.assertEqual(self.bundles(), [])

    def test_path_traversal_ids_are_neutralised(self):
        self.assertEqual(safe_name("../../etc/passwd"), "etcpasswd")
        with self.assertRaises(ValueError):
            safe_name("../..")


class SessionTest(Base):
    def send(self, app, mid, minutes, data=None):
        self.clock[0] = T0 + timedelta(minutes=minutes)
        self.client.contents[mid] = data or jpeg(color=(len(mid) * 10, 0, 0))
        return self.post(app, img_event(mid))

    def test_photos_within_window_make_one_bundle(self):
        app = self.app()
        self.send(app, "m1", 0)
        self.send(app, "m2", 9)
        self.send(app, "m3", 18)  # 前の1枚から9分なので窓の内(最初から数えない)
        bs = self.bundles()
        self.assertEqual(len(bs), 1)
        self.assertEqual((bs[0]["pages"], bs[0]["status"]), (3, "pages_registered"))
        self.assertTrue(bs[0]["file"].startswith("line://" + USER + "/2026-11-03/m1"))

    def test_gap_over_window_starts_new_bundle(self):
        app = self.app()
        self.send(app, "m1", 0)
        self.send(app, "m2", 10)   # ちょうど10分は窓の内
        self.send(app, "m3", 20.5)  # 10分を超えたので別の束
        bs = self.bundles()
        self.assertEqual([b["pages"] for b in bs], [2, 1])
        self.assertNotEqual(bs[0]["sha256"], bs[1]["sha256"])

    def test_different_senders_do_not_mix(self):
        app = self.app()
        other = "U" + "b" * 32
        self.client.contents.update(m1=jpeg(), m2=jpeg("red"))
        self.post(app, img_event("m1"), img_event("m2", user=other))
        self.assertEqual([b["pages"] for b in self.bundles()], [1, 1])

    def test_pages_registered_with_absolute_paths_and_unknown_kind(self):
        app = self.app()
        self.send(app, "m1", 0)
        self.send(app, "m2", 1)
        with Ledger(self.db) as led:
            pages = led.list_pages(1)
        self.assertEqual([p["page_no"] for p in pages], [1, 2])
        self.assertTrue(all(p["kind"] == "不明" and p["image_source"] == "line" for p in pages))
        self.assertTrue(all(Path(p["image_path"]).exists() for p in pages))

    def test_redelivery_is_not_counted_twice(self):
        app = self.app()
        self.send(app, "m1", 0)
        code, res = self.send(app, "m1", 1)
        self.assertEqual(res.replies, [])
        self.assertEqual(self.bundles()[0]["pages"], 1)

    def test_redelivery_after_window_does_not_open_new_bundle(self):
        app = self.app()
        self.send(app, "m1", 0)
        self.send(app, "m1", 30)
        self.assertEqual(len(self.bundles()), 1)

    def test_gps_in_exif_becomes_site_position(self):
        app = self.app()
        self.send(app, "m1", 0)  # 位置なし
        with Ledger(self.db) as led:
            self.assertIsNone(led.get_site("line-1"))
        self.send(app, "m2", 1, jpeg(gps=(35.6895, 139.6917)))
        with Ledger(self.db) as led:
            site = led.get_site("line-1")
            self.assertAlmostEqual(site.lat, 35.6895, places=3)
            self.assertAlmostEqual(site.lng, 139.6917, places=3)
            self.assertTrue(site.name.startswith("LINE受信 2026-11-03"))

    def test_first_position_wins(self):
        app = self.app()
        self.send(app, "m1", 0, jpeg(gps=(35.0, 139.0)))
        self.send(app, "m2", 1, jpeg(gps=(36.0, 140.0)))
        with Ledger(self.db) as led:
            self.assertAlmostEqual(led.get_site("line-1").lat, 35.0, places=3)

    def test_location_message_sets_site(self):
        app = self.app()
        loc = {"type": "message", "replyToken": "t", "source": {"userId": USER},
               "message": {"type": "location", "id": "L1", "latitude": 35.5, "longitude": 139.5,
                           "address": "東京都どこか"}}
        code, res = self.post(app, loc)
        with Ledger(self.db) as led:
            site = led.get_site("line-1")
        self.assertEqual((site.lat, site.lng, site.address), (35.5, 139.5, "東京都どこか"))
        self.assertEqual(res.replies[0][1], "位置を受け取りました")

    def test_text_is_kept_as_note_only(self):
        app = self.app()
        ev = {"type": "message", "replyToken": "t", "source": {"userId": USER},
              "message": {"type": "text", "id": "T1", "text": "裏口の看板"}}
        code, res = self.post(app, ev)
        self.assertEqual(res.replies[0][1], "メモを受け取りました")
        lines = (self.inbox / USER / "2026-11-03" / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["text"], "裏口の看板")
        self.assertEqual(self.bundles()[0]["pages"], 0)

    def test_session_state_survives_restart(self):
        self.send(self.app(), "m1", 0)
        self.send(self.app(), "m2", 5)  # 別の LineApp(再起動相当)でも同じ束
        self.assertEqual([b["pages"] for b in self.bundles()], [2])


class ReplyTest(Base):
    def test_six_photos_one_delivery_one_reply(self):
        app = self.app()
        evs = []
        for i in range(6):
            self.client.contents[f"m{i}"] = jpeg(color=(i * 30, 0, 0))
            evs.append(img_event(f"m{i}"))
        code, res = self.post(app, *evs)
        self.assertEqual(len(res.replies), 1)
        self.assertEqual(res.replies[0], ("tok-m5", "受け取りました。写真 6 枚。看板カルテの下書きを作っています\n" + reply_text.ask_site()))
        self.assertEqual(self.client.replies, list(res.replies))

    def test_count_is_cumulative_across_deliveries(self):
        app = self.app()
        for i in range(3):
            self.client.contents[f"m{i}"] = jpeg(color=(i * 30, 0, 0))
        self.post(app, img_event("m0"), img_event("m1"))
        _, res = self.post(app, img_event("m2"))
        self.assertEqual(res.replies[0][1], "受け取りました。写真 3 枚。看板カルテの下書きを作っています\n" + reply_text.ask_site())

    def test_no_greeting_or_ticket_number(self):
        text = reply_text.receipt(6)
        for banned in ("こんにちは", "ありがとう", "受付番号", "No.", "少々"):
            self.assertNotIn(banned, text)

    def test_reply_failure_is_reported_not_raised(self):
        app = self.app()
        self.client.contents["m1"] = jpeg()

        def boom(token, text, **kw):
            raise IOError("LINE 側のエラー")
        self.client.reply = boom
        code, res = self.post(app, img_event("m1"))
        self.assertEqual(code, 200)
        self.assertTrue(any(e.startswith("reply:") for e in res.errors))
        self.assertEqual(self.bundles()[0]["pages"], 1)  # 保存は済んでいる

    def test_empty_when_nothing_to_say(self):
        self.assertEqual(reply_text.receipt(0), "")


class HttpTest(Base):
    def serve(self):
        app = self.app()
        srv = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_http_signature_and_paths(self):
        base = self.serve()
        self.client.contents["m1"] = jpeg()
        body = body_of(img_event("m1"))
        self.assertEqual(post(base + "/line/webhook", SECRET, body), 200)
        self.assertEqual(post(base + "/line/webhook", SECRET, body, signature="bad"), 401)
        self.assertEqual(post(base + "/other", SECRET, body), 404)
        self.assertEqual(self.bundles()[0]["pages"], 1)

    def test_simulate_files_roundtrip(self):
        imgs = self.root / "imgs"
        imgs.mkdir()
        for i in range(2):
            (imgs / f"{i}.jpg").write_bytes(jpeg(color=(i * 50, 0, 0)))
        content = self.root / "content"
        app = LineApp(SECRET, DirContentClient(content), lambda: Ledger(self.db), self.inbox,
                      now=lambda: self.clock[0])
        srv = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        plan = build_deliveries(sorted(imgs.iterdir()), content, run_id="t", location=(35.0, 139.0))
        for b in plan.deliveries:
            self.assertEqual(post(f"http://127.0.0.1:{srv.server_address[1]}/line/webhook", SECRET, b), 200)
        bs = self.bundles()
        self.assertEqual([b["pages"] for b in bs], [2])
        with Ledger(self.db) as led:
            self.assertEqual(led.get_site("line-1").lat, 35.0)


class CliTest(unittest.TestCase):
    def test_simulate_in_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "imgs").mkdir()
            (root / "imgs" / "a.jpg").write_bytes(jpeg())
            (root / "imgs" / "b.jpg").write_bytes(jpeg("blue"))
            rc = cli_main(["--db", str(root / "l.sqlite"), "--inbox", str(root / "inbox"), "simulate",
                           "--images", str(root / "imgs"), "--content-dir", str(root / "c")])
            self.assertEqual(rc, 0)
            with Ledger(root / "l.sqlite") as led:
                self.assertEqual([b["pages"] for b in led.list_bundles()], [2])
            self.assertEqual(len(list((root / "inbox").glob("*/*/*.jpg"))), 2)


if __name__ == "__main__":
    unittest.main()

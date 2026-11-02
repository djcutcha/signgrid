import tempfile
import unittest
import zipfile
from pathlib import Path

from kanban_karte.geo import fetch_gis as F


def fake_zip(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, b"x")


class FetchGisTest(unittest.TestCase):
    def test_fetch_extract_count_and_skip(self):
        calls = []

        def download(url, dest):
            calls.append(url)
            if url.endswith("001752732.zip"):
                names = ["13/13104_x/13104_youto.shp", "13/13105_y/13105_youto.shp"]
            else:
                names = ["11/11222_a/11222_youto.shp", "11/11222_a/11222_bouka.shp"]
            fake_zip(dest, names)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            counts = F.fetch(root, download=download, out=lambda s: None)
            self.assertEqual(counts, {"saitama": 1, "tokyo": 2})
            self.assertEqual(len(calls), 2)
            self.assertTrue((root / "raw" / "001752729.zip").exists())
            F.fetch(root, download=download, out=lambda s: None)   # 2回目は通信しない
            self.assertEqual(len(calls), 2)

    def test_zip_slip_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            z = Path(td) / "a.zip"
            fake_zip(z, ["../evil.txt"])
            with self.assertRaises(ValueError):
                F.extract_zip(z, Path(td) / "out")


if __name__ == "__main__":
    unittest.main()

"""Checks for reel.py's Instagram post handling (every carousel slide, no login). Run: python test_reel.py
Temp folders only, no network: yt-dlp and Gemini are replaced by fakes."""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / ".claude" / "skills" / "reel-watch" / "scripts"))
import reel  # noqa: E402

POST = "https://www.instagram.com/p/DAbCdEfGhIj/?igsh=abc"
INFO = {"description": "3 tools you need", "uploader": "Some One", "channel": "someone", "title": "Post by someone"}


def fake_ytdlp(files, returncode=1, stderr="ERROR: [Instagram] B: No video formats found!\n"):
    """Stands in for `yt-dlp -o <dir>/%(playlist_index)s.%(ext)s`: writes `files` ({name: bytes or dict}) there."""
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        out = Path(cmd[cmd.index("-o") + 1]).parent
        for name, data in files.items():
            (out / name).write_bytes(json.dumps(data).encode() if isinstance(data, dict) else data)
        return subprocess.CompletedProcess(cmd, returncode, "", stderr)
    return run, calls


class InstagramPost(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, True)

    def fetch(self, files, **kw):
        run, calls = fake_ytdlp(files, **kw)
        with mock.patch.object(reel.subprocess, "run", run):
            result = reel.fetch_instagram_post(POST, self.work)
        self.assertFalse((self.work / "post").exists(), "the temp folder is always removed")
        return result, calls[0]

    def test_mixed_carousel_keeps_every_slide_in_order(self):
        (kind, paths, meta), cmd = self.fetch({
            "0.info.json": {**INFO, "playlist_count": 3}, "1.info.json": INFO, "2.info.json": INFO, "3.info.json": INFO,
            "1.jpg": b"photo1", "2.jpg": b"cover of the video", "2.mp4": b"video2", "3.webp": b"photo3"})
        self.assertEqual(kind, "slides")
        self.assertEqual([p.name for p in paths], ["image_01.jpg", "video_02.mp4", "image_03.webp"])
        self.assertEqual([p.read_bytes() for p in paths], [b"photo1", b"video2", b"photo3"])
        self.assertEqual(meta["description"], "3 tools you need")
        self.assertNotIn("note", meta)
        # the fix itself: photos have no "format", so yt-dlp must not stop at them, and must save them
        self.assertIn("--ignore-no-formats-error", cmd)
        self.assertIn("--write-thumbnail", cmd)

    def test_photo_carousel_is_images(self):
        (kind, paths, _), _ = self.fetch({"0.info.json": {**INFO, "playlist_count": 2}, "1.jpg": b"a", "2.jpg": b"b"})
        self.assertEqual((kind, [p.name for p in paths]), ("images", ["image_01.jpg", "image_02.jpg"]))

    def test_single_photo_post(self):
        (kind, paths, meta), _ = self.fetch({"NA.info.json": INFO, "NA.jpg": b"a"})
        self.assertEqual((kind, [p.name for p in paths]), ("images", ["image_01.jpg"]))
        self.assertEqual(meta["uploader"], "Some One")

    def test_video_post_is_a_plain_video_without_its_cover(self):
        (kind, paths, _), _ = self.fetch({"NA.info.json": INFO, "NA.jpg": b"cover", "NA.mp4": b"v"}, returncode=0)
        self.assertEqual((kind, [p.name for p in paths]), ("video", ["video.mp4"]))
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), ["video.mp4"])

    def test_several_videos_are_slides(self):
        (kind, paths, _), _ = self.fetch({"1.mp4": b"a", "2.mp4": b"b"}, returncode=0)
        self.assertEqual((kind, [p.name for p in paths]), ("slides", ["video_01.mp4", "video_02.mp4"]))

    def test_missing_slides_are_reported(self):
        (kind, paths, meta), _ = self.fetch({"0.info.json": {**INFO, "playlist_count": 3}, "1.jpg": b"a", "2.jpg": b"b"})
        self.assertEqual(len(paths), 2)
        self.assertEqual(meta["note"], "Instagram post: only 2 of its 3 slides could be downloaded (missing: slide 3)")

    def test_slides_keep_their_own_numbers_after_a_missing_one(self):
        (kind, paths, meta), _ = self.fetch({"0.info.json": {**INFO, "playlist_count": 3}, "2.jpg": b"b", "3.jpg": b"c"})
        self.assertEqual((kind, [p.name for p in paths]), ("images", ["image_02.jpg", "image_03.jpg"]))
        self.assertIn("(missing: slide 1)", meta["note"])

    def test_one_surviving_video_of_a_carousel_is_still_a_carousel(self):
        (kind, paths, meta), _ = self.fetch({"0.info.json": {**INFO, "playlist_count": 3}, "2.jpg": b"cover",
                                             "2.mp4": b"v"})
        self.assertEqual((kind, [p.name for p in paths]), ("slides", ["video_02.mp4"]))
        self.assertIn("(missing: slide 1, 3)", meta["note"])

    def test_nothing_downloaded_raises_the_real_error(self):
        err = "ERROR: [Instagram] X: Requested content is not available, rate-limit reached or login required\n"
        run, _ = fake_ytdlp({}, stderr=err + "ERROR: [Instagram] B: No video formats found!\n")
        with mock.patch.object(reel.subprocess, "run", run), self.assertRaises(RuntimeError) as e:
            reel.fetch_instagram_post(POST, self.work)
        self.assertIn("login required", str(e.exception))
        self.assertEqual(list(self.work.iterdir()), [])

    def test_cookies_are_passed_to_ytdlp(self):
        run, calls = fake_ytdlp({"NA.jpg": b"a"})
        with mock.patch.object(reel.subprocess, "run", run):
            reel.fetch_instagram_post(POST, self.work, cookies="c.txt")
        self.assertEqual(calls[0][1:3], ["--cookies", "c.txt"])


class FetchOrder(unittest.TestCase):
    def order(self, url):
        tried = []

        def fail(name):
            def f(*a, **k):
                tried.append(name + ("+cookies" if k.get("cookies") or (len(a) > 2 and a[2]) else ""))
                raise RuntimeError("blocked")
            return f
        cookies = tempfile.NamedTemporaryFile(delete=False)
        cookies.close()
        with mock.patch.dict(reel.os.environ, {"REEL_IG_COOKIES": cookies.name}), \
                mock.patch.object(reel, "fetch_instagram_post", fail("post")), \
                mock.patch.object(reel, "fetch_ytdlp", fail("video")), \
                mock.patch.object(reel, "fetch_kkinstagram", fail("kkinstagram")), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(reel.FetchFailed):
            reel.fetch([url], Path(tempfile.mkdtemp()))
        Path(cookies.name).unlink()
        return tried

    def test_post_tries_the_login_before_the_first_slide_only_proxy(self):
        self.assertEqual(self.order(POST), ["post", "post+cookies", "kkinstagram"])

    def test_reel_order_is_unchanged(self):
        self.assertEqual(self.order("https://www.instagram.com/reel/DAbCdEfGhIj/"),
                         ["video", "kkinstagram", "video+cookies"])

    def test_which_links_are_posts(self):
        self.assertTrue(reel.is_instagram_post("https://www.instagram.com/p/DAbCdEfGhIj/"))
        self.assertTrue(reel.is_instagram_post("https://instagram.com/someone/p/DAbCdEfGhIj"))
        self.assertFalse(reel.is_instagram_post("https://www.instagram.com/reel/DAbCdEfGhIj/"))
        self.assertFalse(reel.is_instagram_post("https://www.tiktok.com/@a/video/1"))


class Gemini(unittest.TestCase):
    def test_slides_go_in_order_labelled_and_big_videos_are_uploaded_then_deleted(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "image_01.jpg").write_bytes(b"i" * 10)
        (d / "video_02.mp4").write_bytes(b"v" * 30)
        (d / "video_03.mp4").write_bytes(b"w" * 30)
        sent, deleted = {}, []

        def generate(parts, prompt, key):
            sent.update(parts=parts, prompt=prompt)
            raise RuntimeError("quota")  # uploads are deleted even when Gemini fails
        with mock.patch.object(reel, "GEMINI_INLINE_MAX", 50), \
                mock.patch.object(reel, "gemini_upload", lambda p, mime, key: {"name": f"files/{p.stem}", "uri": f"u/{p.stem}"}), \
                mock.patch.object(reel, "gemini_request", lambda method, url, key: deleted.append((method, url))), \
                mock.patch.object(reel, "gemini_generate", generate), self.assertRaises(RuntimeError):
            reel.gemini_watch_slides(sorted(d.iterdir()), {"description": "cap"}, "k")
        parts = sent["parts"]
        self.assertEqual([p.get("text") for p in parts[::2]], ["Slide 1:", "Slide 2:", "Slide 3:"])
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/jpeg")
        self.assertEqual(parts[3]["inline_data"]["mime_type"], "video/mp4")  # 10 + 30 bytes fit in 50
        self.assertEqual(parts[5]["file_data"], {"mime_type": "video/mp4", "file_uri": "u/video_03"})
        self.assertIn("carousel", sent["prompt"])
        self.assertIn("cap", sent["prompt"])
        self.assertEqual(deleted, [("DELETE", f"{reel.GEMINI_API}/v1beta/files/video_03")])

    def test_photo_labels_only_when_a_slide_is_missing(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        for name in ("image_01.jpg", "image_02.jpg", "image_04.jpg"):
            (d / name).write_bytes(b"i")
        labels = []
        generate = lambda parts, prompt, key: (labels.append([p["text"] for p in parts if "text" in p]), "m")
        with mock.patch.object(reel, "gemini_generate", generate):
            reel.gemini_watch_images([d / "image_01.jpg", d / "image_02.jpg"], {}, "k")  # a whole album: unchanged
            reel.gemini_watch_images([d / "image_01.jpg", d / "image_02.jpg", d / "image_04.jpg"], {}, "k")
        self.assertEqual(labels, [[], ["Slide 1:", "Slide 2:", "Slide 4:"]])


class SlidesPipeline(unittest.TestCase):
    """main() on a photo + video carousel with the local engine (needs ffmpeg or imageio-ffmpeg)."""

    def test_local_engine_lists_every_slide_with_its_number_and_shares_the_frame_budget(self):
        ff = shutil.which("ffmpeg")
        if not ff:
            try:
                import imageio_ffmpeg
                ff = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                self.skipTest("needs ffmpeg or imageio-ffmpeg")
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        src = root / "src"
        src.mkdir()
        subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64", "-frames:v", "1",
                        str(src / "a.jpg")], check=True)
        subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=10",
                        str(src / "b.mp4")], check=True)

        def fake_fetch(sources, work, lowres=False):  # slide 4 of this carousel failed to download
            paths = [work / name for name in ("image_01.jpg", "video_02.mp4", "video_03.mp4", "video_05.mp4")]
            for p in paths:
                shutil.copy(src / ("a.jpg" if p.suffix == ".jpg" else "b.mp4"), p)
            return "slides", paths, {"description": "caption"}, "yt-dlp"
        out = io.StringIO()
        argv = ["reel.py", POST, "--engine", "local", "--no-transcript", "--max-frames", "6",
                "--out-root", str(root / "reels")]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(reel, "fetch", fake_fetch), \
                mock.patch.object(reel, "instagram_caption", lambda url: None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            reel.main()
        text = out.getvalue()
        self.assertIn("kind: slides", text)
        self.assertIn("slides: 4", text)
        self.assertRegex(text, r"slide 1: image  .*image_01\.jpg")
        self.assertIn("slide 2: video, 2.0s", text)
        self.assertIn("slide 5: video, 2.0s", text)
        self.assertRegex(text, r"frames_05[/\\]001\.jpg")
        work = Path(text.split("REEL_DIR: ")[1].splitlines()[0])
        manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([(s["slide"], s["type"]) for s in manifest["slides"]],
                         [(1, "image"), (2, "video"), (3, "video"), (5, "video")])
        per_video = [len(s["frames"]) for s in manifest["slides"] if s["type"] == "video"]
        self.assertTrue(all(per_video), per_video)
        self.assertLessEqual(sum(per_video), 6, "the videos share --max-frames")


if __name__ == "__main__":
    unittest.main()

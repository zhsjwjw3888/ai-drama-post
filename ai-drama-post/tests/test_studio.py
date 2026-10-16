import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("studio", Path(__file__).resolve().parents[1] / "scripts/studio.py")
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="漫剧 test ")
        self.root = Path(self.temp.name)
        self.plan = self.root / "plan.json"
        self.media = self.root / "片段.mp4"
        self.media.write_bytes(b"source")
        self.data = {"version": 1, "width": 320, "height": 240, "fps": 24, "codec": "h264",
                     "output": "renders/out.mp4", "clips": [{"id": "one", "path": "片段.mp4", "start": 0, "duration": .5}]}
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        studio.save(self.plan, self.data)

    def approve(self):
        studio.review(self.plan, "one", ["visual", "audio", "timing"], "Actual fixture review")

    def test_path_escape_and_absolute_refused(self):
        for value in ("../outside.mp4", "/outside.mp4", "C:/outside.mp4"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                studio.local(self.root, value)

    def test_invalid_numbers_ids_and_overwrite(self):
        for field, value in (("width", 319), ("fps", float("nan")), ("codec", "unknown"), ("output", "片段.mp4")):
            original = self.data[field]
            self.data[field] = value; self.write()
            with self.subTest(field=field), self.assertRaises(ValueError): studio.load(self.plan)
            self.data[field] = original
        self.data["clips"] *= 2; self.write()
        with self.assertRaises(ValueError): studio.load(self.plan)

    def test_gate_requires_all_checks(self):
        studio.review(self.plan, "one", ["visual"], "Viewed frames")
        with self.assertRaises(ValueError): studio.gate(self.plan)
        studio.review(self.plan, "one", ["audio", "timing"], "Listened with picture")
        self.assertEqual(studio.gate(self.plan)["status"], "passed")

    def test_media_change_invalidates_review(self):
        self.approve(); self.media.write_bytes(b"changed")
        with self.assertRaises(ValueError): studio.gate(self.plan)
        studio.review(self.plan, "one", ["visual"], "New picture review")
        with self.assertRaises(ValueError): studio.gate(self.plan)

    def test_trim_change_invalidates_review(self):
        self.approve(); self.data["clips"][0]["duration"] = .25; self.write()
        with self.assertRaises(ValueError): studio.gate(self.plan)

    def test_no_empty_review(self):
        with self.assertRaises(ValueError): studio.review(self.plan, "one", ["visual"], " ")

    def test_remote_queue_rejected_before_network(self):
        for url in ("https://example.com", "http://example.com", "http://user@localhost:8188"):
            with self.assertRaises(ValueError): studio.queue(url)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
class MediaTests(PlanTests):
    def setUp(self):
        super().setUp()
        self.media.unlink()
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=24",
                        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.media)], check=True)

    def test_render_silent_input_and_chinese_path(self):
        self.approve()
        before = studio.digest(self.media)
        result = studio.render(self.plan)
        self.assertTrue(result["final_review_required"])
        meta = studio.probe(Path(result["candidate"]))
        self.assertEqual(next(x for x in meta["streams"] if x["codec_type"] == "audio")["sample_rate"], "48000")
        self.assertLess(abs(float(meta["format"]["duration"]) - .5), .1)
        self.assertEqual(before, studio.digest(self.media))
        with self.assertRaises(ValueError): studio.render(self.plan)

    def test_source_overrun_rejected(self):
        self.data["clips"][0]["duration"] = 2; self.write()
        with self.assertRaises(ValueError): studio.check(self.plan)

    def test_subtitles_and_external_mix(self):
        sub = self.root / "字幕.ass"
        sub.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 240\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,18,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.00,0:00:00.50,Default,,0,0,0,,Test\n", encoding="utf-8")
        mix = self.root / "mix.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(mix)], check=True)
        self.data.update(subtitles=sub.name, mix=mix.name); self.write(); self.approve()
        self.assertTrue(Path(studio.render(self.plan)["candidate"]).exists())
        sub.write_text("{\\p1} drawing", encoding="utf-8")
        with self.assertRaises(ValueError): studio.check(self.plan)


if __name__ == "__main__":
    unittest.main()

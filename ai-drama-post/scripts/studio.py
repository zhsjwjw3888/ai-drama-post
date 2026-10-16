"""Local post-production helpers. No model download or automatic visual approval."""
import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def local(root, value):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ":" in value:
        raise ValueError("Expected project-relative path")
    result = (root / value).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Path escapes project")
    return result


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError("Invalid time or size")
    return value


def load(plan):
    plan = Path(plan).resolve()
    p = json.loads(plan.read_text(encoding="utf-8"))
    if p.get("version") != 1:
        raise ValueError("Unsupported plan version")
    for field in ("width", "height"):
        n = number(p[field], True)
        if int(n) != n or n % 2:
            raise ValueError("Dimensions must be positive even integers")
    if not 1 <= number(p["fps"], True) <= 120 or p.get("codec") not in ("h264", "h265"):
        raise ValueError("Invalid fps or codec")
    root = plan.parent
    output = local(root, p["output"])
    if output.suffix.lower() != ".mp4":
        raise ValueError("Output must be MP4")
    clips = p.get("clips")
    if not isinstance(clips, list) or not clips:
        raise ValueError("At least one clip required")
    ids = set()
    for c in clips:
        if not isinstance(c.get("id"), str) or not c["id"] or c["id"] in ids:
            raise ValueError("Clip IDs must be unique nonempty strings")
        ids.add(c["id"])
        local(root, c["path"])
        number(c.get("start", 0))
        number(c["duration"], True)
    for field in ("mix", "subtitles"):
        if p.get(field):
            local(root, p[field])
    inputs = [local(root, c["path"]) for c in clips]
    inputs += [local(root, p[x]) for x in ("mix", "subtitles") if p.get(x)]
    if output in inputs:
        raise ValueError("Output cannot overwrite input")
    return root, p


def probe(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ], text=True, encoding="utf-8"))


def check(plan):
    root, p = load(plan)
    for c in p["clips"]:
        meta = probe(local(root, c["path"]))
        video = next((s for s in meta["streams"] if s["codec_type"] == "video"), None)
        if not video:
            raise ValueError("No video: " + c["id"])
        duration = float(video.get("duration", meta["format"].get("duration", 0)))
        if c.get("start", 0) + c["duration"] > duration + .02:
            raise ValueError("Clip exceeds source: " + c["id"])
        if round(c["duration"] * p["fps"]) < 1:
            raise ValueError("Clip shorter than one output frame")
    total = sum(round(c["duration"] * p["fps"]) / p["fps"] for c in p["clips"])
    if p.get("mix"):
        meta = probe(local(root, p["mix"]))
        if not any(s["codec_type"] == "audio" for s in meta["streams"]):
            raise ValueError("Mix has no audio")
        if float(meta["format"].get("duration", 0)) + .02 < total:
            raise ValueError("Mix shorter than timeline")
    if p.get("subtitles"):
        sub = local(root, p["subtitles"])
        if sub.suffix.lower() != ".ass" or not sub.is_file():
            raise ValueError("Expected existing ASS subtitles")
        if "\\p1" in sub.read_text(encoding="utf-8-sig").lower():
            raise ValueError("Review ASS drawing content before use")
    return root, p, total


def review(plan, clip, checks, evidence):
    root, p = load(plan)
    c = next((x for x in p["clips"] if x["id"] == clip), None)
    if not c or not evidence.strip():
        raise ValueError("Valid clip and actual review evidence required")
    path = root / "reviews.json"
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    fingerprint = digest(local(root, c["path"]))
    old = records.get(clip, {})
    if old.get("sha256") != fingerprint or old.get("clip") != c:
        old = {"sha256": fingerprint, "clip": c, "checks": {}, "evidence": []}
    old["checks"].update({k: True for k in checks})
    old["evidence"].append(evidence)
    records[clip] = old
    save(path, records)


def gate(plan):
    root, p = load(plan)
    records = json.loads((root / "reviews.json").read_text(encoding="utf-8"))
    for c in p["clips"]:
        r = records.get(c["id"], {})
        if r.get("sha256") != digest(local(root, c["path"])) or r.get("clip") != c:
            raise ValueError("Missing or stale review: " + c["id"])
        if not r.get("evidence") or not all(r.get("checks", {}).get(k) is True for k in ("visual", "audio", "timing")):
            raise ValueError("Review incomplete: " + c["id"])
    return {"status": "passed", "clips": len(p["clips"]), "scope": "Recorded source reviews only; final timeline review still required"}


def run(args, cwd=None):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-n", *args], check=True, cwd=cwd)


def verify(path):
    path = Path(path).resolve()
    meta = probe(path)
    run(["-xerror", "-i", str(path), "-f", "null", "-"])
    result = {"sha256": digest(path), "complete_decode": "passed", "streams": meta["streams"],
              "duration": meta["format"].get("duration"), "scope": "Technical validation, not visual or listening approval"}
    save(path.with_suffix(".verification.json"), result)
    return result


def render(plan):
    root, p, total = check(plan)
    gate(plan)
    output = local(root, p["output"])
    partial = output.with_suffix(".partial.mp4")
    if output.exists() or partial.exists():
        raise ValueError("Output/partial already exists; use a new version")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="drama-") as tmp:
        work = Path(tmp)
        for i, c in enumerate(p["clips"]):
            src = local(root, c["path"])
            has_audio = any(s["codec_type"] == "audio" for s in probe(src)["streams"])
            args = ["-ss", str(c.get("start", 0)), "-i", str(src)]
            if not has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            duration = round(c["duration"] * p["fps"]) / p["fps"]
            vf = f"scale={p['width']}:{p['height']}:force_original_aspect_ratio=increase,crop={p['width']}:{p['height']},fps={p['fps']},setsar=1"
            run([*args, "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0", "-t", str(duration),
                 "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-ar", "48000", "-ac", "2", str(work / f"{i}.mp4")])
        (work / "concat.txt").write_text("\n".join(f"file '{i}.mp4'" for i in range(len(p["clips"]))), encoding="utf-8")
        args = ["-f", "concat", "-safe", "1", "-i", "concat.txt"]
        if p.get("mix"):
            args += ["-i", str(local(root, p["mix"]))]
        args += ["-map", "0:v:0", "-map", "1:a:0" if p.get("mix") else "0:a:0"]
        if p.get("subtitles"):
            shutil.copy2(local(root, p["subtitles"]), work / "captions.ass")
            args += ["-vf", "subtitles=captions.ass"]
        args += ["-t", str(total), "-c:v", "libx264" if p["codec"] == "h264" else "libx265",
                 "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(partial)]
        run(args, cwd=work)
    result = verify(partial)
    video = next(s for s in result["streams"] if s["codec_type"] == "video")
    audio = next((s for s in result["streams"] if s["codec_type"] == "audio"), {})
    if ((video["width"], video["height"]) != (p["width"], p["height"])
            or abs(float(result["duration"]) - total) > .15
            or abs(float(Fraction(video["avg_frame_rate"])) - p["fps"]) > .001
            or video["codec_name"] != ("h264" if p["codec"] == "h264" else "hevc")
            or audio.get("sample_rate") != "48000" or audio.get("channels") != 2):
        raise ValueError("Output metadata mismatch; partial retained")
    partial.rename(output)
    partial.with_suffix(".verification.json").rename(output.with_suffix(".verification.json"))
    return {"candidate": str(output), "final_review_required": True}


def queue(url):
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.username:
        raise ValueError("Only local HTTP ComfyUI endpoints supported")
    with urlopen(url.rstrip("/") + "/queue", timeout=5) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("doctor")
    q = commands.add_parser("queue"); q.add_argument("--url", default="http://127.0.0.1:8188")
    init = commands.add_parser("init"); init.add_argument("directory", type=Path)
    for name in ("check", "gate", "render", "review"):
        sub = commands.add_parser(name); sub.add_argument("plan", type=Path)
        if name == "review":
            sub.add_argument("--clip", required=True)
            sub.add_argument("--checks", nargs="+", choices=("visual", "audio", "timing"), required=True)
            sub.add_argument("--evidence", required=True)
    v = commands.add_parser("verify"); v.add_argument("path", type=Path)
    a = parser.parse_args()
    try:
        if a.cmd == "doctor":
            result = {"python": sys.version.split()[0], "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe")}
        elif a.cmd == "init":
            a.directory.mkdir(parents=True, exist_ok=True)
            if (a.directory / "plan.json").exists():
                raise ValueError("Plan exists; refusing overwrite")
            for folder in ("media", "renders"):
                (a.directory / folder).mkdir(exist_ok=True)
            save(a.directory / "plan.json", {"version": 1, "output": "renders/episode-v1.mp4", "width": 720, "height": 1280,
                 "fps": 24, "codec": "h264", "clips": [{"id": "shot01", "path": "media/shot01.mp4", "start": 0, "duration": 3}]})
            result = {"plan": str(a.directory / "plan.json"), "next": "Supply your media; init does not generate video"}
        elif a.cmd == "queue": result = queue(a.url)
        elif a.cmd == "check": result = {"seconds": check(a.plan)[2], "status": "valid"}
        elif a.cmd == "review":
            review(a.plan, a.clip, a.checks, a.evidence); result = {"recorded": a.clip, "automatic_quality_judgment": False}
        elif a.cmd == "gate": result = gate(a.plan)
        elif a.cmd == "render": result = render(a.plan)
        else: result = verify(a.path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

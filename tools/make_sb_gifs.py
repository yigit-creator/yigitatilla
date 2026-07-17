#!/usr/bin/env python3
"""Download public X videos and turn them into branded 300x300 GIFs."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "SB_World_Cup_GIFs_SPAIN"
SRC = ROOT / ".sb_sources"
LOGO = ROOT / "tools" / "SB_favicon_white.png"

ITEMS = [
    {"n": 1, "handle": "MercatoBlaugra", "id": "2077174374042325328"},
    {"n": 2, "handle": "anclarz", "id": "2077136294694576467", "start": 0.0, "end": 1.0},
    {"n": 3, "handle": "taypedri", "id": "2077142098453872949"},
    {"n": 4, "handle": "angrygavi", "id": "2077164497781850365"},
    {"n": 5, "handle": "M_S0f1__91218", "id": "2077160400567582812"},
    {"n": 6, "handle": "vancitylex", "id": "2077139484668264669"},
    {"n": 7, "handle": "GxlDePaulinho", "id": "2077111629402030428"},
    {"n": 8, "handle": "FCBKayy", "id": "2077398919185567835"},
    {"n": 9, "handle": "jakubklyszejko", "id": "2077135794628641212"},
    {"n": 10, "handle": "BaldeWaves", "id": "2077126244303321601"},
    {"n": 11, "handle": "Ruf_ayi1", "id": "2077338614342533505"},
    {"n": 12, "handle": "patelsatyen24", "id": "2077270405761630682", "start": 8.0, "end": 14.0},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "*/*",
}


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def ffprobe_duration(path: Path) -> float:
    p = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ])
    try:
        return float(p.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Could not read duration for {path}: {p.stdout}") from exc


def iter_urls(obj: Any, parent: dict[str, Any] | None = None) -> Iterable[tuple[str, dict[str, Any] | None]]:
    if isinstance(obj, dict):
        for value in obj.values():
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                yield value, obj
            else:
                yield from iter_urls(value, obj)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_urls(value, parent)


def media_score(url: str, ctx: dict[str, Any] | None) -> tuple[int, int, int]:
    lower = url.lower()
    if ".mp4" in lower or "video.twimg.com" in lower:
        kind = 3
    elif ".m3u8" in lower:
        kind = 2
    else:
        kind = 0

    bitrate = 0
    area = 0
    if ctx:
        for key in ("bitrate", "bit_rate", "bitRate"):
            try:
                bitrate = max(bitrate, int(ctx.get(key) or 0))
            except (TypeError, ValueError):
                pass
        try:
            w = int(ctx.get("width") or 0)
            h = int(ctx.get("height") or 0)
            area = max(area, w * h)
        except (TypeError, ValueError):
            pass
    m = re.search(r"/(\d{2,5})x(\d{2,5})/", url)
    if m:
        area = max(area, int(m.group(1)) * int(m.group(2)))
    return kind, area, bitrate


def candidate_urls(data: Any) -> list[str]:
    found: list[tuple[tuple[int, int, int], str]] = []
    seen: set[str] = set()
    for url, ctx in iter_urls(data):
        if url in seen:
            continue
        seen.add(url)
        score = media_score(url, ctx)
        if score[0] > 0:
            found.append((score, url.replace("\\u0026", "&")))
    found.sort(reverse=True)
    return [url for _, url in found]


def fetch_json(url: str) -> Any | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=45)
        print(f"GET {url} -> {r.status_code} {r.headers.get('content-type','')}")
        if r.ok:
            return r.json()
    except Exception as exc:
        print(f"JSON fetch failed for {url}: {exc}")
    return None


def download_http(url: str, target: Path) -> bool:
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.unlink(missing_ok=True)
    try:
        with requests.get(url, headers=HEADERS, timeout=(30, 180), stream=True, allow_redirects=True) as r:
            ctype = r.headers.get("content-type", "").lower()
            print(f"MEDIA {url} -> {r.status_code} {ctype} final={r.url}")
            if not r.ok:
                return False
            if "text/html" in ctype or "application/json" in ctype:
                return False
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        if tmp.stat().st_size < 10_000:
            print(f"Rejected tiny download: {tmp.stat().st_size} bytes")
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(target)
        return True
    except Exception as exc:
        print(f"Download failed for {url}: {exc}")
        tmp.unlink(missing_ok=True)
        return False


def download_with_ffmpeg(url: str, target: Path) -> bool:
    target.unlink(missing_ok=True)
    p = run([
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-headers", f"User-Agent: {HEADERS['User-Agent']}\r\n",
        "-i", url, "-c", "copy", "-movflags", "+faststart", str(target)
    ], check=False)
    if p.returncode == 0 and target.exists() and target.stat().st_size > 10_000:
        return True
    print(p.stdout[-4000:])
    target.unlink(missing_ok=True)
    return False


def download_with_ytdlp(x_url: str, target: Path) -> bool:
    prefix = target.with_suffix("")
    for old in prefix.parent.glob(prefix.name + ".*"):
        if old != target:
            old.unlink(missing_ok=True)
    p = run([
        sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings",
        "--retries", "4", "--fragment-retries", "4",
        "-f", "bv*+ba/b", "--merge-output-format", "mp4",
        "-o", str(prefix) + ".%(ext)s", x_url
    ], check=False)
    print(p.stdout[-5000:])
    candidates = sorted(prefix.parent.glob(prefix.name + ".*"), key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    for cand in candidates:
        if cand.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"} and cand.stat().st_size > 10_000:
            if cand != target:
                run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(cand), "-c", "copy", str(target)])
                cand.unlink(missing_ok=True)
            return True
    return False


def obtain_source(item: dict[str, Any]) -> tuple[Path, str]:
    n = item["n"]
    handle = item["handle"]
    tid = item["id"]
    target = SRC / f"{n:02d}_{handle}_{tid}.mp4"
    if target.exists() and target.stat().st_size > 10_000:
        return target, "cache"

    x_url = f"https://x.com/{handle}/status/{tid}"
    api_urls = [
        f"https://api.fxtwitter.com/2/status/{tid}",
        f"https://api.fxtwitter.com/{handle}/status/{tid}",
        f"https://api.fxtwitter.com/{tid}",
    ]
    for api_url in api_urls:
        data = fetch_json(api_url)
        if not data:
            continue
        for media_url in candidate_urls(data):
            if ".m3u8" in media_url.lower():
                ok = download_with_ffmpeg(media_url, target)
            else:
                ok = download_http(media_url, target) or download_with_ffmpeg(media_url, target)
            if ok:
                return target, f"FxTwitter API: {api_url}"

    for direct_url in (
        f"https://d.fixupx.com/{handle}/status/{tid}",
        f"https://fixupx.com/{handle}/status/{tid}.mp4",
        f"https://d.fxtwitter.com/{handle}/status/{tid}",
        f"https://fxtwitter.com/{handle}/status/{tid}.mp4",
    ):
        if download_http(direct_url, target) or download_with_ffmpeg(direct_url, target):
            return target, f"direct media: {direct_url}"

    if download_with_ytdlp(x_url, target):
        return target, "yt-dlp"

    raise RuntimeError(f"Could not download {x_url}")


def make_gif(source: Path, output: Path, start: float, end: float | None) -> tuple[float, float]:
    full_duration = ffprobe_duration(source)
    start = min(max(0.0, start), max(0.0, full_duration - 0.05))
    if end is None:
        clip_duration = max(0.05, full_duration - start)
    else:
        clip_duration = max(0.05, min(end, full_duration) - start)

    output.unlink(missing_ok=True)
    filter_complex = (
        "[0:v]fps=15,"
        "scale=300:300:force_original_aspect_ratio=increase:flags=lanczos,"
        "crop=300:300:(iw-ow)/2:(ih-oh)/2,setsar=1[base];"
        "[1:v]scale=48:48:flags=lanczos[logo];"
        "[base][logo]overlay=W-w-16:H-h-16:format=auto,split[s0][s1];"
        "[s0]palettegen=max_colors=256:stats_mode=diff[p];"
        "[s1][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-ss", f"{start:.3f}", "-i", str(source),
        "-loop", "1", "-i", str(LOGO),
        "-t", f"{clip_duration:.3f}",
        "-filter_complex", filter_complex,
        "-an", "-loop", "0", str(output),
    ]
    p = run(cmd, check=False)
    if p.returncode != 0:
        print(p.stdout[-6000:])
        raise RuntimeError(f"GIF export failed: {output.name}")
    if shutil.which("gifsicle"):
        optimized = output.with_suffix(".optimized.gif")
        q = run(["gifsicle", "-O3", "--careful", str(output), "-o", str(optimized)], check=False)
        if q.returncode == 0 and optimized.exists() and optimized.stat().st_size > 0:
            optimized.replace(output)
        else:
            optimized.unlink(missing_ok=True)
    return full_duration, clip_duration


def main() -> None:
    if not LOGO.exists():
        raise FileNotFoundError(LOGO)
    OUT.mkdir(parents=True, exist_ok=True)
    SRC.mkdir(parents=True, exist_ok=True)

    manifest_lines = [
        "SB World Cup GIFs — Spain",
        "Format: 300x300 px, 15 fps, continuous loop, SB favicon 48x48 px at bottom-right (16 px margins)",
        "",
    ]
    errors: list[str] = []
    for item in ITEMS:
        n, handle, tid = item["n"], item["handle"], item["id"]
        output = OUT / f"{n:02d}_{handle}_{tid}.gif"
        start = float(item.get("start", 0.0))
        end = float(item["end"]) if "end" in item else None
        print(f"\n=== {n:02d} @{handle} / {tid} ===", flush=True)
        try:
            source, method = obtain_source(item)
            full_duration, clip_duration = make_gif(source, output, start, end)
            manifest_lines.append(
                f"{output.name} | source=https://x.com/{handle}/status/{tid} | "
                f"source_duration={full_duration:.3f}s | clip={start:.3f}-{start+clip_duration:.3f}s | "
                f"download={method} | size={output.stat().st_size} bytes"
            )
        except Exception as exc:
            msg = f"{n:02d} @{handle}: {exc}"
            errors.append(msg)
            manifest_lines.append("ERROR | " + msg)
            print(msg, file=sys.stderr, flush=True)

    (OUT / "manifest.txt").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    if errors:
        raise RuntimeError("Some GIFs failed:\n" + "\n".join(errors))
    produced = sorted(OUT.glob("*.gif"))
    if len(produced) != len(ITEMS):
        raise RuntimeError(f"Expected {len(ITEMS)} GIFs, produced {len(produced)}")
    print(f"\nDone: {len(produced)} GIFs in {OUT}")


if __name__ == "__main__":
    main()

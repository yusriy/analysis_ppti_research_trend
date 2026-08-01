#!/usr/bin/env python3
"""Build a short, evidence-grounded PPTI Research Brief and optional narration."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

import cv2
from PIL import Image, ImageDraw, ImageFont


REPO_DIR = Path(__file__).resolve().parents[1]
PODCAST_DIR = Path(__file__).resolve().parent
LATEST_DIR = PODCAST_DIR / "latest"
ARCHIVE_DIR = PODCAST_DIR / "archive"
ARTIFACT_PATH = REPO_DIR / "publication_strategy_report" / "artifact.json"
DEFAULT_ENV_PATH = REPO_DIR / ".secrets" / "elevenlabs.env"
REPORT_URL = (
    "https://yusriy.github.io/analysis_ppti_research_trend/"
    "ppti_publication_strategy_report.html"
)
RESEARCH_REPORTS_URL = "https://indtech.usm.my/index.php/research/reports"
MIN_WORDS = 115
MAX_WORDS = 210
MAX_DURATION_SECONDS = 120.0
TARGET_WORDS_PER_MINUTE = 145.0
COVER_PALETTE = {
    1: ("Mist blue", "#e8f0f6"),
    2: ("Soft sage", "#e9f0e8"),
    3: ("Muted blush", "#f3e9e9"),
    4: ("Pale lavender", "#eeeaf4"),
    5: ("Warm sand", "#f3eee3"),
    6: ("Institutional white", "#f9fbff"),
    7: ("Powder blue", "#e6eef4"),
    8: ("Muted mint", "#e6f0ed"),
    9: ("Soft parchment", "#f2ede3"),
    10: ("Dusty rose", "#f1e7e9"),
    11: ("Soft lilac", "#ece9f2"),
    12: ("Silver blue", "#e8edf2"),
}


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE settings without evaluating shell syntax."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            os.environ.setdefault(key, value)


def first_row(datasets: dict[str, list[dict]], name: str) -> dict:
    rows = datasets.get(name, [])
    if not rows:
        raise ValueError(f"Required report dataset is empty: {name}")
    return rows[0]


def month_label(period_end: str) -> str:
    parsed = date.fromisoformat(period_end)
    return parsed.strftime("%B %Y")


def rolling_window_label(period_end: str, months: int = 24) -> str:
    """Return the inclusive month range for a completed rolling window."""
    parsed = date.fromisoformat(period_end)
    start_index = parsed.year * 12 + parsed.month - 1 - (months - 1)
    start = date(start_index // 12, start_index % 12 + 1, 1)
    return f"{start:%b %Y}–{parsed:%b %Y}"


def spoken_percent(value: float) -> str:
    return f"{value * 100:.1f} percent"


def is_meaningful_theme(row: dict) -> bool:
    """Reject unsupported one-word fragments from the featured theme slot."""
    label = str(row.get("keyword", "")).strip()
    words = re.findall(r"[A-Za-z0-9]+", label)
    return int(row.get("publications", 0)) >= 2 or len(words) >= 2


def select_latest_month_signals(
    rows: list[dict], kind: str, limit: int = 3
) -> list[dict]:
    """Select current-month signals with strong rolling-window support."""
    if kind == "keyword":
        candidates = [row for row in rows if is_meaningful_theme(row)]
        key = lambda row: (
            -int(row.get("latest_month_publications", 0)),
            -int(row.get("publications", 0)),
            -float(row.get("citation_weight", 0)),
            -int(row.get("journal_reach", 0)),
            str(row.get("keyword", "")).lower(),
        )
    elif kind == "journal":
        candidates = rows
        key = lambda row: (
            -int(row.get("latest_month_publications", 0)),
            -int(row.get("publications", 0)),
            -float(row.get("citations", 0)),
            str(row.get("journal", "")).lower(),
        )
    else:
        raise ValueError(f"Unknown monthly signal kind: {kind}")

    if not candidates:
        raise ValueError(f"No eligible latest-month {kind} signals available")
    return sorted(candidates, key=key)[:limit]


def select_latest_month_signal(rows: list[dict], kind: str) -> dict:
    """Return the leading signal for backward-compatible callers."""
    return select_latest_month_signals(rows, kind, limit=1)[0]


def natural_list(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return " and ".join(values)
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def sign_phrase(value: int, noun: str) -> str:
    if value > 0:
        return f"an increase of {value} {noun}"
    if value < 0:
        return f"a decrease of {abs(value)} {noun}"
    return f"no change in {noun}"


def build_episode(artifact: dict[str, Any]) -> dict[str, Any]:
    datasets = artifact["snapshot"]["datasets"]
    summary = first_row(datasets, "summary")
    comparison = first_row(datasets, "monthly_comparison")
    period_end = comparison["current_period_end"]
    issue_label = month_label(period_end)

    all_activity = [
        row
        for row in datasets.get("monthly_activity", [])
        if row.get("series") == "All publications"
    ]
    if len(all_activity) < 2:
        raise ValueError("At least two months of publication activity are required")
    latest_activity = all_activity[-1]
    previous_activity = all_activity[-2]

    journals = select_latest_month_signals(
        datasets.get("latest_month_journal_signals", []),
        "journal",
    )
    keywords = select_latest_month_signals(
        datasets.get("latest_month_keyword_signals", []),
        "keyword",
    )
    journal = journals[0]
    keyword = keywords[0]

    if comparison.get("comparison_available"):
        publication_delta = int(comparison.get("publication_delta") or 0)
        citation_delta = int(comparison.get("citation_delta") or 0)
        comparison_sentence = (
            "Compared with the preceding monthly edition, the rolling portfolio recorded "
            f"{sign_phrase(publication_delta, 'publications')} and "
            f"{sign_phrase(citation_delta, 'citations')}."
        )
    else:
        comparison_sentence = (
            "This is the first edition under the complete-pagination and "
            "article-deduplication method, so it establishes the monthly baseline."
        )

    latest_delta = int(latest_activity["publications"]) - int(
        previous_activity["publications"]
    )
    if latest_delta > 0:
        activity_phrase = "rose"
    elif latest_delta < 0:
        activity_phrase = "fell"
    else:
        activity_phrase = "held steady"

    journal_names = [str(row["journal"]) for row in journals]
    journal_sentence = (
        "Three journals represented in the latest completed month are "
        f"{natural_list(journal_names)}."
    )

    keyword_names = [str(row["keyword"]) for row in keywords]
    keyword_publication_counts = [str(int(row["publications"])) for row in keywords]
    keyword_sentence = (
        "Three recurring themes represented that month are "
        f"{natural_list(keyword_names)}. They appear in "
        f"{natural_list(keyword_publication_counts)} rolling-window publications, "
        "respectively."
    )

    script = " ".join(
        [
            f"Welcome to the PPTI Research Brief for {issue_label}.",
            (
                "This edition covers the rolling twenty-four-month period ending "
                f"{date.fromisoformat(period_end).strftime('%-d %B %Y')}."
            ),
            (
                f"The portfolio contains {int(summary['distinct_publications'])} distinct "
                f"Scopus-indexed publications from {int(summary['contributors'])} PPTI "
                f"contributors, appearing across {int(summary['journals'])} journals."
            ),
            (
                f"Open-access articles account for "
                f"{spoken_percent(float(summary['open_access_share']))} of the portfolio."
            ),
            (
                f"In the latest completed month, publication output {activity_phrase} from "
                f"{int(previous_activity['publications'])} to "
                f"{int(latest_activity['publications'])} articles."
            ),
            comparison_sentence,
            journal_sentence,
            keyword_sentence,
            (
                "These signals show both established strength and activity beyond the "
                "highest-volume outlets and themes."
            ),
            "Read the full PPTI publication trends report for the complete evidence.",
        ]
    )
    script = re.sub(r"\s+", " ", script).strip()
    word_count = len(re.findall(r"\b[\w’'-]+\b", script))
    if not MIN_WORDS <= word_count <= MAX_WORDS:
        raise ValueError(
            f"Podcast script has {word_count} words; expected {MIN_WORDS}–{MAX_WORDS}"
        )

    estimated_duration = word_count / TARGET_WORDS_PER_MINUTE * 60
    return {
        "episode_title": f"PPTI Research Brief — {issue_label}",
        "period_end": period_end,
        "issue_label": issue_label,
        "rolling_window_label": rolling_window_label(period_end),
        "script": script,
        "word_count": word_count,
        "estimated_duration_seconds": round(estimated_duration, 1),
        "selected_journals": [
            {
                "name": row["journal"],
                "tier": row.get("tier"),
                "publications": int(row["publications"]),
                "latest_month_publications": int(
                    row.get("latest_month_publications", 0)
                ),
                "citations": int(row.get("citations", 0)),
            }
            for row in journals
        ],
        "selected_keywords": [
            {
                "name": row["keyword"],
                "tier": row.get("tier"),
                "publications": int(row["publications"]),
                "latest_month_publications": int(
                    row.get("latest_month_publications", 0)
                ),
                "journal_reach": int(row.get("journal_reach", 0)),
                "citations_per_publication": float(
                    row.get("citations_per_publication", 0)
                ),
            }
            for row in keywords
        ],
        "latest_month": {
            "month": latest_activity["month"],
            "publications": int(latest_activity["publications"]),
            "previous_publications": int(previous_activity["publications"]),
        },
        "summary": summary,
    }


def archive_previous_episode(new_period_end: str) -> None:
    metadata_path = LATEST_DIR / "episode_metadata.json"
    if not metadata_path.exists():
        return
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    previous_period = metadata.get("period_end")
    if not previous_period or previous_period == new_period_end:
        return
    destination = ARCHIVE_DIR / previous_period[:7]
    destination.mkdir(parents=True, exist_ok=True)
    for source in LATEST_DIR.iterdir():
        if source.is_file():
            shutil.copy2(source, destination / source.name)
    print(f"Archived prior podcast edition as {destination.relative_to(REPO_DIR)}")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Return Helvetica for clean, consistent digital-media typography."""
    path = Path("/System/Library/Fonts/Helvetica.ttc")
    if path.exists():
        return ImageFont.truetype(str(path), size, index=1 if bold else 0)
    fallback = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    if fallback.exists():
        return ImageFont.truetype(str(fallback), size)
    return ImageFont.load_default()


def fit_image(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    converted = image.convert("RGBA")
    converted.thumbnail(box, Image.Resampling.LANCZOS)
    return converted


def cover_theme(period_end: str) -> dict[str, str]:
    month = date.fromisoformat(period_end).month
    name, background = COVER_PALETTE[month]
    return {"name": name, "background": background}


def blend_hex(base: str, overlay: str, amount: float) -> str:
    base_rgb = tuple(int(base[index : index + 2], 16) for index in (1, 3, 5))
    overlay_rgb = tuple(
        int(overlay[index : index + 2], 16) for index in (1, 3, 5)
    )
    blended = tuple(
        round(base_value * (1 - amount) + overlay_value * amount)
        for base_value, overlay_value in zip(base_rgb, overlay_rgb)
    )
    return "#" + "".join(f"{value:02x}" for value in blended)


def qr_code_image(value: str, size: int) -> Image.Image:
    matrix = cv2.QRCodeEncoder_create().encode(value)
    matrix = cv2.copyMakeBorder(
        matrix,
        4,
        4,
        4,
        4,
        cv2.BORDER_CONSTANT,
        value=255,
    )
    return Image.fromarray(matrix).convert("RGB").resize(
        (size, size),
        Image.Resampling.NEAREST,
    )


def centered_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    width: int,
    selected_font: ImageFont.ImageFont,
    fill: str,
    spacing: int = 12,
) -> int:
    approximate_chars = max(12, int(width / max(selected_font.size * 0.55, 1)))
    wrapped = "\n".join(
        textwrap.fill(paragraph, width=approximate_chars)
        for paragraph in text.splitlines()
    )
    bounds = draw.multiline_textbbox(
        (0, 0),
        wrapped,
        font=selected_font,
        spacing=spacing,
        align="center",
    )
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    draw.multiline_text(
        ((1080 - text_width) / 2, y),
        wrapped,
        font=selected_font,
        fill=fill,
        spacing=spacing,
        align="center",
    )
    return y + text_height


def build_cover(episode: dict[str, Any], destination: Path) -> None:
    width, height = 1080, 1920
    theme = cover_theme(episode["period_end"])
    background = theme["background"]
    signal_panel = blend_hex(background, "#9ab3cc", 0.24)
    signal_outline = blend_hex(background, "#6e8faf", 0.35)
    image = Image.new("RGB", (width, height), "#102f57")
    draw = ImageDraw.Draw(image)
    for y in range(height):
        blend = y / height
        color = (
            int(16 + 10 * blend),
            int(47 + 14 * blend),
            int(87 + 26 * blend),
        )
        draw.line((0, y, width, y), fill=color)

    draw.rounded_rectangle(
        (70, 80, 1010, 1840),
        radius=42,
        fill=background,
        outline="#c59a31",
        width=6,
    )
    usm = fit_image(Image.open(REPO_DIR / "assets" / "usm_logo.png"), (190, 190))
    ppti = fit_image(Image.open(REPO_DIR / "assets" / "ppti_logo.png"), (190, 190))
    image.paste(usm, (125, 145), usm)
    image.paste(ppti, (765, 145), ppti)
    draw.text(
        (540, 205),
        "RESEARCH TRENDS\nCOMMITTEE 2025",
        anchor="mm",
        align="center",
        font=font(30, bold=True),
        fill="#173f73",
        spacing=7,
    )

    y = 430
    y = centered_wrapped_text(
        draw,
        "PPTI Research Brief",
        y,
        830,
        font(72, bold=True),
        "#14213d",
        spacing=15,
    )
    y += 34
    issue_font = font(46, bold=True)
    issue_bounds = draw.textbbox((0, 0), episode["issue_label"], font=issue_font)
    issue_width = issue_bounds[2] - issue_bounds[0]
    issue_height = issue_bounds[3] - issue_bounds[1]
    badge_width = issue_width + 84
    badge_height = issue_height + 38
    badge_left = (width - badge_width) / 2
    draw.rounded_rectangle(
        (badge_left, y, badge_left + badge_width, y + badge_height),
        radius=badge_height / 2,
        fill="#1f64b5",
    )
    draw.text(
        (width / 2, y + badge_height / 2),
        episode["issue_label"],
        anchor="mm",
        font=issue_font,
        fill="#ffffff",
    )
    draw.text(
        (width / 2, y + badge_height + 28),
        f"ROLLING WINDOW  ·  {episode['rolling_window_label'].upper()}",
        anchor="ma",
        font=font(22, bold=True),
        fill="#526278",
    )
    y += badge_height + 88

    summary = episode["summary"]
    metrics = [
        ("PUBLICATIONS", f"{int(summary['distinct_publications']):,}"),
        ("CONTRIBUTORS", f"{int(summary['contributors']):,}"),
        ("OPEN ACCESS", f"{float(summary['open_access_share']):.1%}"),
    ]
    x_positions = (230, 540, 850)
    for x, (label, value) in zip(x_positions, metrics):
        draw.text(
            (x, y),
            value,
            anchor="ma",
            font=font(58, bold=True),
            fill="#14213d",
        )
        draw.text(
            (x, y + 75),
            label,
            anchor="ma",
            font=font(22, bold=True),
            fill="#5b6b82",
        )

    y += 230
    draw.rounded_rectangle(
        (135, y, 945, y + 380),
        radius=30,
        fill=signal_panel,
        outline=signal_outline,
        width=3,
    )
    draw.text(
        (540, y + 42),
        "THIS MONTH'S SIGNALS",
        anchor="ma",
        font=font(24, bold=True),
        fill="#173f73",
    )
    journal_text = natural_list(
        [row["name"] for row in episode["selected_journals"]]
    )
    theme_text = natural_list(
        [row["name"] for row in episode["selected_keywords"]]
    )
    label_font = font(20, bold=True)
    for label, label_y, label_fill in (
        ("JOURNALS", y + 82, "#1f64b5"),
        ("THEMES", y + 247, "#a86f08"),
    ):
        label_bounds = draw.textbbox((0, 0), label, font=label_font)
        label_width = label_bounds[2] - label_bounds[0] + 48
        draw.rounded_rectangle(
            (
                (width - label_width) / 2,
                label_y,
                (width + label_width) / 2,
                label_y + 38,
            ),
            radius=19,
            fill=label_fill,
        )
        draw.text(
            (width / 2, label_y + 19),
            label,
            anchor="mm",
            font=label_font,
            fill="#ffffff",
        )
    centered_wrapped_text(
        draw,
        journal_text,
        y + 130,
        710,
        font(30),
        "#27364d",
        spacing=10,
    )
    centered_wrapped_text(
        draw,
        theme_text,
        y + 295,
        710,
        font(32, bold=True),
        "#173f73",
        spacing=10,
    )
    qr = qr_code_image(RESEARCH_REPORTS_URL, 158)
    image.paste(qr, (145, 1622))
    draw.text(
        (350, 1635),
        "SCAN FOR THE FULL RESEARCH REPORTS",
        font=font(24, bold=True),
        fill="#173f73",
    )
    draw.multiline_text(
        (350, 1685),
        "indtech.usm.my/index.php/\nresearch/reports",
        font=font(27),
        fill="#27364d",
        spacing=7,
    )
    draw.text(
        (540, 1810),
        "School of Industrial Technology · Universiti Sains Malaysia",
        anchor="mm",
        font=font(21),
        fill="#526278",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)


def request_narration(
    script: str,
    api_key: str,
    voice_id: str,
    model_id: str,
    output_format: str,
    speed: float,
) -> tuple[dict[str, Any], dict[str, str]]:
    encoded_voice_id = urllib.parse.quote(voice_id, safe="")
    encoded_format = urllib.parse.quote(output_format, safe="")
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{encoded_voice_id}"
        f"/with-timestamps?output_format={encoded_format}"
    )
    body = json.dumps(
        {
            "text": script,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.1,
                "use_speaker_boost": True,
                "speed": speed,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "xi-api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
            response_headers = {
                "character_cost": response.headers.get("character-cost", ""),
                "request_id": response.headers.get("request-id", ""),
                "trace_id": response.headers.get("x-trace-id", ""),
            }
            return payload, response_headers
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"ElevenLabs returned HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach ElevenLabs: {exc.reason}") from exc


def srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def alignment_to_srt(alignment: dict[str, Any]) -> tuple[str, float]:
    characters = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not characters or not (len(characters) == len(starts) == len(ends)):
        raise ValueError("ElevenLabs response did not include usable timing alignment")

    rendered_text = "".join(characters)
    word_spans = list(re.finditer(r"\S+", rendered_text))
    captions: list[tuple[int, int, str]] = []
    group_start = 0
    current_words: list[str] = []
    for index, match in enumerate(word_spans):
        current_words.append(match.group())
        phrase = " ".join(current_words)
        sentence_end = bool(re.search(r"[.!?][\"']?$", match.group()))
        if len(current_words) >= 10 or len(phrase) >= 60 or sentence_end:
            captions.append((group_start, index, phrase))
            group_start = index + 1
            current_words = []
    if current_words:
        captions.append(
            (group_start, len(word_spans) - 1, " ".join(current_words))
        )

    lines: list[str] = []
    for number, (start_word, end_word, phrase) in enumerate(captions, start=1):
        first_char = word_spans[start_word].start()
        last_char = word_spans[end_word].end() - 1
        lines.extend(
            [
                str(number),
                f"{srt_timestamp(float(starts[first_char]))} --> "
                f"{srt_timestamp(float(ends[last_char]))}",
                phrase,
                "",
            ]
        )
    return "\n".join(lines), float(ends[-1])


def parse_srt_timestamp(value: str) -> float:
    hours, minutes, remainder = value.split(":")
    seconds, milliseconds = remainder.split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000
    )


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    captions: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = block.splitlines()
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        start, end = lines[1].split(" --> ", 1)
        captions.append(
            (
                parse_srt_timestamp(start),
                parse_srt_timestamp(end),
                " ".join(lines[2:]),
            )
        )
    return captions


def caption_frame(base: Image.Image, caption: str) -> Image.Image:
    frame = base.copy().convert("RGB")
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle(
        (105, 1365, 975, 1585),
        radius=30,
        fill="#102f57",
        outline="#c59a31",
        width=4,
    )
    centered_wrapped_text(
        draw,
        caption,
        1415,
        760,
        font(38, bold=True),
        "#ffffff",
        spacing=11,
    )
    return frame


def probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required to validate podcast duration")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def normalize_audio(audio_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to normalize podcast audio")
    normalized_path = audio_path.with_name(f"{audio_path.stem}.normalized.mp3")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-af",
            "loudnorm=I=-16:LRA=7:TP=-1.5",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-b:a",
            "128k",
            str(normalized_path),
        ],
        check=True,
    )
    normalized_path.replace(audio_path)


def render_social_video(latest_dir: Path, duration_seconds: float) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found; skipped the social video.")
        return
    captions = parse_srt(latest_dir / "captions.srt")
    base = Image.open(latest_dir / "cover.png").convert("RGB")

    timeline: list[tuple[float, str | None]] = []
    cursor = 0.0
    for start, end, caption in captions:
        if start > cursor + 0.01:
            timeline.append((start - cursor, None))
        timeline.append((max(0.04, end - start), caption))
        cursor = max(cursor, end)
    if duration_seconds > cursor + 0.01:
        timeline.append((duration_seconds - cursor, None))

    with tempfile.TemporaryDirectory(prefix="ppti-podcast-video-") as temp_name:
        temp_dir = Path(temp_name)
        concat_lines: list[str] = []
        last_frame: Path | None = None
        for index, (segment_duration, caption) in enumerate(timeline):
            frame_path = temp_dir / f"frame_{index:04d}.png"
            frame = caption_frame(base, caption) if caption else base
            frame.save(frame_path, format="PNG", optimize=True)
            concat_lines.extend(
                [
                    f"file '{frame_path}'",
                    f"duration {segment_duration:.3f}",
                ]
            )
            last_frame = frame_path
        if last_frame is None:
            raise ValueError("No video timeline could be constructed")
        concat_lines.append(f"file '{last_frame}'")
        concat_path = temp_dir / "frames.txt"
        concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-i",
            str(latest_dir / "ppti_research_brief.mp3"),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-tune",
            "stillimage",
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(latest_dir / "ppti_research_brief_vertical.mp4"),
        ]
        subprocess.run(command, check=True)


def write_text_outputs(episode: dict[str, Any]) -> None:
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    script = episode["script"]
    (LATEST_DIR / "script.txt").write_text(script + "\n", encoding="utf-8")
    (LATEST_DIR / "transcript.txt").write_text(
        f"{episode['episode_title']}\n\n{script}\n\nFull report: {REPORT_URL}\n",
        encoding="utf-8",
    )
    social_caption = (
        f"{episode['episode_title']}\n\n"
        "This brief highlights current publication activity, including "
        f"{natural_list([row['name'] for row in episode['selected_journals']])}, "
        "and the themes "
        f"{natural_list([row['name'] for row in episode['selected_keywords']])}.\n\n"
        f"Read the complete report: {REPORT_URL}\n\n"
        f"USM research reports: {RESEARCH_REPORTS_URL}\n\n"
        "#USM #PPTI #ResearchTrends #Scopus #ResearchImpact"
    )
    (LATEST_DIR / "social_caption.txt").write_text(
        social_caption + "\n",
        encoding="utf-8",
    )
    build_cover(episode, LATEST_DIR / "cover.png")


def write_archive_readme() -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    editions = sorted(
        [path for path in ARCHIVE_DIR.iterdir() if path.is_dir()],
        reverse=True,
    )
    lines = [
        "# Archived PPTI Research Brief editions",
        "",
        "Each folder preserves the narration, transcript, captions, social copy, "
        "cover artwork, and metadata from a previous monthly edition.",
    ]
    if editions:
        lines.extend(["", "## Editions", ""])
        for edition in editions:
            lines.append(f"- [{edition.name}]({edition.name}/)")
    else:
        lines.extend(
            [
                "",
                "The first edition will be moved here automatically when a newer monthly "
                "episode is generated.",
            ]
        )
    (ARCHIVE_DIR / "README.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the monthly PPTI Research Brief."
    )
    parser.add_argument("--artifact", type=Path, default=ARTIFACT_PATH)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(os.environ.get("ELEVENLABS_ENV_FILE", DEFAULT_ENV_PATH)),
    )
    parser.add_argument(
        "--script-only",
        action="store_true",
        help="Create the editorial package without calling ElevenLabs.",
    )
    parser.add_argument(
        "--require-audio",
        action="store_true",
        help="Fail instead of falling back to script-only mode when credentials are absent.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate audio even when the current script already has narration.",
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    episode = build_episode(artifact)
    archive_previous_episode(episode["period_end"])
    write_text_outputs(episode)
    write_archive_readme()

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    model_id = os.environ.get(
        "ELEVENLABS_MODEL_ID",
        "eleven_multilingual_v2",
    ).strip()
    output_format = os.environ.get(
        "ELEVENLABS_OUTPUT_FORMAT",
        "mp3_44100_128",
    ).strip()
    speed = float(os.environ.get("ELEVENLABS_SPEED", "0.98"))
    script_hash = hashlib.sha256(episode["script"].encode("utf-8")).hexdigest()
    cover_hash = hashlib.sha256(
        (LATEST_DIR / "cover.png").read_bytes()
    ).hexdigest()

    metadata_path = LATEST_DIR / "episode_metadata.json"
    existing_metadata: dict[str, Any] = {}
    if metadata_path.exists():
        existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    cover_changed = existing_metadata.get("cover_sha256") != cover_hash
    audio_path = LATEST_DIR / "ppti_research_brief.mp3"
    exact_audio_match = (
        audio_path.exists()
        and existing_metadata.get("script_sha256") == script_hash
        and existing_metadata.get("voice_id") == voice_id
        and existing_metadata.get("model_id") == model_id
    )
    recoverable_partial_audio = (
        audio_path.exists()
        and (LATEST_DIR / "captions.srt").exists()
        and (LATEST_DIR / "timing.json").exists()
        and existing_metadata.get("script_sha256") == script_hash
        and not existing_metadata.get("voice_id")
    )
    same_audio = exact_audio_match or recoverable_partial_audio
    if (
        (LATEST_DIR / "ppti_research_brief_vertical.mp4").exists()
        and existing_metadata.get("cover_sha256") != cover_hash
    ):
        (LATEST_DIR / "ppti_research_brief_vertical.mp4").unlink()

    audio_generated = False
    audio_normalized = bool(existing_metadata.get("audio_normalized"))
    duration_seconds: float | None = None
    response_headers: dict[str, str] = {}
    if args.script_only:
        if audio_path.exists() and existing_metadata.get("script_sha256") == script_hash:
            status = "audio_ready"
            duration_seconds = existing_metadata.get("duration_seconds")
            response_headers = existing_metadata.get("elevenlabs_response", {})
            voice_id = existing_metadata.get("voice_id") or voice_id
            model_id = existing_metadata.get("model_id") or model_id
            output_format = existing_metadata.get("output_format") or output_format
            speed = float(existing_metadata.get("speed") or speed)
        else:
            status = "script_ready"
    elif not api_key or not voice_id:
        if args.require_audio:
            raise SystemExit(
                f"ElevenLabs credentials are missing. Add them to {args.env_file}."
            )
        status = "awaiting_credentials"
        print(
            f"Podcast script package created. Add ElevenLabs credentials to "
            f"{args.env_file} to generate audio."
        )
    elif same_audio and not args.force:
        status = "audio_ready"
        duration_seconds = existing_metadata.get("duration_seconds")
        response_headers = existing_metadata.get("elevenlabs_response", {})
        saved_alignment = json.loads(
            (LATEST_DIR / "timing.json").read_text(encoding="utf-8")
        )
        refreshed_captions, aligned_duration = alignment_to_srt(saved_alignment)
        captions_path = LATEST_DIR / "captions.srt"
        captions_changed = (
            not captions_path.exists()
            or captions_path.read_text(encoding="utf-8") != refreshed_captions
        )
        if captions_changed:
            captions_path.write_text(refreshed_captions, encoding="utf-8")
            (LATEST_DIR / "ppti_research_brief_vertical.mp4").unlink(
                missing_ok=True
            )
        if cover_changed:
            (LATEST_DIR / "ppti_research_brief_vertical.mp4").unlink(
                missing_ok=True
            )
        if duration_seconds is None:
            duration_seconds = aligned_duration
        if not audio_normalized:
            normalize_audio(audio_path)
            audio_normalized = True
            duration_seconds = probe_duration(audio_path)
            (LATEST_DIR / "ppti_research_brief_vertical.mp4").unlink(
                missing_ok=True
            )
        if not (LATEST_DIR / "ppti_research_brief_vertical.mp4").exists():
            render_social_video(LATEST_DIR, duration_seconds)
        print("Existing narration matches the current script; ElevenLabs call skipped.")
    else:
        payload, response_headers = request_narration(
            episode["script"],
            api_key,
            voice_id,
            model_id,
            output_format,
            speed,
        )
        audio_path.write_bytes(base64.b64decode(payload["audio_base64"]))
        alignment = payload.get("normalized_alignment") or payload.get("alignment")
        captions, duration_seconds = alignment_to_srt(alignment or {})
        if duration_seconds > MAX_DURATION_SECONDS:
            raise RuntimeError(
                f"Narration is {duration_seconds:.1f} seconds; maximum is "
                f"{MAX_DURATION_SECONDS:.0f} seconds."
            )
        (LATEST_DIR / "captions.srt").write_text(captions, encoding="utf-8")
        (LATEST_DIR / "timing.json").write_text(
            json.dumps(alignment, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        normalize_audio(audio_path)
        audio_normalized = True
        duration_seconds = probe_duration(audio_path)
        render_social_video(LATEST_DIR, duration_seconds)
        audio_generated = True
        status = "audio_ready"

    metadata = {
        "episode_title": episode["episode_title"],
        "period_end": episode["period_end"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_report_generated_at": artifact["manifest"]["generatedAt"],
        "source_report_url": REPORT_URL,
        "status": status,
        "word_count": episode["word_count"],
        "estimated_duration_seconds": episode["estimated_duration_seconds"],
        "duration_seconds": duration_seconds,
        "script_sha256": script_hash,
        "cover_sha256": cover_hash,
        "institutional_reports_url": RESEARCH_REPORTS_URL,
        "cover_theme": cover_theme(episode["period_end"]),
        "selected_journals": episode["selected_journals"],
        "selected_keywords": episode["selected_keywords"],
        "latest_month": episode["latest_month"],
        "audio_generated_this_run": audio_generated,
        "audio_normalized": audio_normalized,
        "voice_id": voice_id or None,
        "model_id": model_id,
        "output_format": output_format,
        "speed": speed,
        "elevenlabs_response": response_headers,
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Built {episode['episode_title']}: {episode['word_count']} words, "
        f"estimated {episode['estimated_duration_seconds']:.0f} seconds; status={status}."
    )


if __name__ == "__main__":
    main()

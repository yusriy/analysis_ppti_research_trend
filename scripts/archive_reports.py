#!/usr/bin/env python3
"""Archive the current monthly HTML reports before replacing them."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, timedelta
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = REPO_DIR / "archive"
STATE_PATH = ARCHIVE_DIR / "report_archive_state.json"
ARCHIVE_README = ARCHIVE_DIR / "README.md"
REPORTS = (
    "ppti_journal_report.html",
    "ppti_keywords_report.html",
    "ppti_publication_strategy_report.html",
)
STRATEGY_ARTIFACT = REPO_DIR / "publication_strategy_report" / "artifact.json"


def current_reporting_period() -> str:
    first_of_month = date.today().replace(day=1)
    return (first_of_month - timedelta(days=1)).isoformat()


def load_state() -> dict[str, str]:
    if not STATE_PATH.exists():
        return {}
    payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return payload.get("current_editions", {})


def write_state(editions: dict[str, str]) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "description": (
            "Tracks the reporting period represented by each current HTML report. "
            "The monthly runner uses this state to archive an edition before replacing it."
        ),
        "current_editions": editions,
    }
    STATE_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def archive_current_reports(target_period: str) -> None:
    editions = load_state()
    if not editions:
        print("Archive state is not initialized; no current report was archived.")
        return

    for report_name in REPORTS:
        source = REPO_DIR / report_name
        previous_period = editions.get(report_name)
        if not source.exists() or not previous_period or previous_period >= target_period:
            continue

        edition_dir = ARCHIVE_DIR / previous_period[:7]
        edition_dir.mkdir(parents=True, exist_ok=True)
        destination = edition_dir / report_name
        shutil.copy2(source, destination)
        print(f"Archived {report_name} as {destination.relative_to(REPO_DIR)}")

        if report_name == "ppti_publication_strategy_report.html" and STRATEGY_ARTIFACT.exists():
            shutil.copy2(
                STRATEGY_ARTIFACT,
                edition_dir / "ppti_publication_strategy_artifact.json",
            )


def finalize_current_reports(target_period: str) -> None:
    editions = load_state()
    for report_name in REPORTS:
        if (REPO_DIR / report_name).exists():
            editions[report_name] = target_period
    write_state(editions)
    write_archive_readme()
    print(f"Current report editions finalized for {target_period}.")


def write_archive_readme() -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Archived PPTI reports",
        "",
        "Each folder preserves the report edition that was current before the next "
        "completed-month update replaced it. The publication strategy archive also "
        "includes its canonical analytical artifact for reproducibility.",
        "",
        "## Current reports",
        "",
        "- [Strategic Journal Targeting](../ppti_journal_report.html)",
        "- [Keyword Impact Report](../ppti_keywords_report.html)",
        "- [PPTI Research Publication Trends and Strategic Positioning](../ppti_publication_strategy_report.html)",
    ]

    edition_dirs = sorted(
        (
            path
            for path in ARCHIVE_DIR.iterdir()
            if path.is_dir() and len(path.name) == 7 and path.name[4] == "-"
        ),
        reverse=True,
    )
    if edition_dirs:
        lines.extend(["", "## Previous editions"])
        for edition_dir in edition_dirs:
            lines.extend(["", f"### {edition_dir.name}", ""])
            for report_name in REPORTS:
                archived = edition_dir / report_name
                if archived.exists():
                    label = report_name.removesuffix(".html").replace("_", " ").title()
                    lines.append(f"- [{label}]({edition_dir.name}/{report_name})")
    else:
        lines.extend(
            [
                "",
                "Previous editions will appear here automatically when the next monthly "
                "report replaces the current edition.",
            ]
        )

    ARCHIVE_README.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "finalize"))
    args = parser.parse_args()
    target_period = current_reporting_period()

    if args.action == "prepare":
        archive_current_reports(target_period)
        write_archive_readme()
    else:
        finalize_current_reports(target_period)


if __name__ == "__main__":
    main()

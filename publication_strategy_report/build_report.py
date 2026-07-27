#!/usr/bin/env python
"""Build the PPTI publication report with month-aware narrative commentary."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd


REPO_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(__file__).resolve().parent
PUBLICATIONS_CSV = REPO_DIR / "data" / "ppti_journal_publications.csv"
SNAPSHOT_HISTORY_JSON = OUTPUT_DIR / "monthly_snapshots.json"
METHODOLOGY_VERSION = "scopus-complete-pagination-deduplicated-v1"

TODAY = pd.Timestamp.today().normalize()
END_DATE = TODAY.replace(day=1) - pd.Timedelta(days=1)
START_DATE = END_DATE - pd.DateOffset(months=24) + pd.Timedelta(days=1)
SOURCE_ID = "scopus_publication_snapshot"
SOURCE_SQL = f"""
SELECT *
FROM scopus_publications
WHERE date(Date) BETWEEN date('{START_DATE:%Y-%m-%d}') AND date('{END_DATE:%Y-%m-%d}')
""".strip()


def compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def keyword_label(value: str) -> str:
    label = value.title()
    replacements = {
        "Ai": "AI",
        "Iot": "IoT",
        "Lca": "LCA",
        "Pha": "PHA",
        "Phb": "PHB",
        "Pcr": "PCR",
    }
    for old, new in replacements.items():
        label = re.sub(rf"\b{old}\b", new, label)
    return label


def records(frame: pd.DataFrame) -> list[dict]:
    clean = frame.copy()
    for column in clean.columns:
        if pd.api.types.is_integer_dtype(clean[column]):
            clean[column] = clean[column].astype(int)
        elif pd.api.types.is_float_dtype(clean[column]):
            clean[column] = clean[column].astype(float).round(4)
    return clean.where(pd.notna(clean), None).to_dict(orient="records")


def load_snapshot_history() -> list[dict]:
    if not SNAPSHOT_HISTORY_JSON.exists():
        return []
    payload = json.loads(SNAPSHOT_HISTORY_JSON.read_text(encoding="utf-8"))
    if payload.get("methodology_version") != METHODOLOGY_VERSION:
        return []
    return payload.get("snapshots", [])


def previous_monthly_snapshot(history: list[dict]) -> dict | None:
    eligible = [
        snapshot
        for snapshot in history
        if snapshot.get("period_end", "") < END_DATE.strftime("%Y-%m-%d")
    ]
    return max(eligible, key=lambda item: item["period_end"]) if eligible else None


def save_monthly_snapshot(snapshot: dict) -> None:
    history = load_snapshot_history()
    history = [
        item for item in history if item.get("period_end") != snapshot["period_end"]
    ]
    history.append(snapshot)
    history = sorted(history, key=lambda item: item["period_end"])[-13:]
    payload = {
        "methodology_version": METHODOLOGY_VERSION,
        "description": (
            "Compact monthly comparison baselines used to vary report commentary. "
            "Raw publication records remain in data/ppti_journal_publications.csv."
        ),
        "snapshots": history,
    }
    SNAPSHOT_HISTORY_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def rank_map(counts: dict[str, int]) -> dict[str, int]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    return {name: index + 1 for index, (name, _) in enumerate(ordered)}


def portfolio_tier(count: int) -> str:
    if count >= 5:
        return "Established (5+)"
    if count >= 2:
        return "Developing (2–4)"
    return "Exploratory (1)"


def tier_summary(
    stats: pd.DataFrame,
    entity_label: str,
    total_associations: int,
) -> pd.DataFrame:
    tier_order = ["Established (5+)", "Developing (2–4)", "Exploratory (1)"]
    summary = (
        stats.groupby("tier", as_index=False, observed=True)
        .agg(
            entities=("tier", "size"),
            publication_associations=("publications", "sum"),
        )
        .set_index("tier")
        .reindex(tier_order, fill_value=0)
        .reset_index()
    )
    summary["portfolio_share"] = (
        summary["publication_associations"] / total_associations
        if total_associations
        else 0.0
    )
    summary["entity_type"] = entity_label
    return summary


def movement_label(current_count: int, previous_count: int | None) -> str:
    if previous_count is None:
        return "Baseline"
    delta = current_count - previous_count
    if previous_count == 0 and current_count > 0:
        return "New"
    if delta > 0:
        return f"↑ +{delta}"
    if delta < 0:
        return f"↓ {delta}"
    return "Stable"


def tier_movement_sentence(
    current_counts: dict[str, int],
    previous_snapshot: dict | None,
    previous_key: str,
    entity_plural: str,
) -> str:
    if previous_snapshot is None:
        return (
            f"This edition establishes the first tier-level baseline for {entity_plural}; "
            "later editions will identify new entrants and movement between exploratory, "
            "developing, and established tiers."
        )

    previous_counts = previous_snapshot.get(previous_key, {})
    new_entities = [
        name for name in current_counts if name not in previous_counts
    ]
    moved_to_developing = [
        name
        for name, count in current_counts.items()
        if count >= 2 and int(previous_counts.get(name, 0)) == 1
    ]
    moved_to_established = [
        name
        for name, count in current_counts.items()
        if count >= 5 and 1 < int(previous_counts.get(name, 0)) < 5
    ]
    changes = sorted(
        (
            (name, count - int(previous_counts.get(name, 0)), count)
            for name, count in current_counts.items()
        ),
        key=lambda item: (-item[1], -item[2], item[0].lower()),
    )
    top_mover = next((item for item in changes if item[1] > 0), None)

    entity_singular = entity_plural[:-1] if entity_plural.endswith("s") else entity_plural
    parts = [
        (
            f"{len(new_entities)} new "
            f"{entity_singular if len(new_entities) == 1 else entity_plural} "
            "entered the rolling portfolio"
        ),
        f"{len(moved_to_developing)} moved from exploratory to developing status",
        f"{len(moved_to_established)} moved into the established tier",
    ]
    sentence = "; ".join(parts) + "."
    if top_mover:
        sentence += (
            f" {top_mover[0]} recorded the largest increase, gaining "
            f"{top_mover[1]} publication{'s' if top_mover[1] != 1 else ''} "
            f"to reach {top_mover[2]}."
        )
    return sentence


def comparison_sentence(
    current_snapshot: dict, previous_snapshot: dict | None
) -> tuple[str, bool]:
    if previous_snapshot is None:
        return (
            "This edition establishes the first monthly baseline produced under the "
            "complete-pagination and article-deduplication methodology; subsequent editions "
            "will report changes against this baseline.",
            False,
        )

    previous_label = pd.Timestamp(previous_snapshot["period_end"]).strftime("%B %Y")
    publication_delta = (
        current_snapshot["metrics"]["publications"]
        - previous_snapshot["metrics"]["publications"]
    )
    citation_delta = (
        current_snapshot["metrics"]["citations"]
        - previous_snapshot["metrics"]["citations"]
    )
    oa_delta_pp = 100 * (
        current_snapshot["metrics"]["oa_share"]
        - previous_snapshot["metrics"]["oa_share"]
    )
    material = (
        abs(publication_delta) >= 3
        or abs(citation_delta) >= 25
        or abs(oa_delta_pp) >= 2
        or current_snapshot["leaders"] != previous_snapshot.get("leaders", {})
    )

    if not material:
        return (
            f"Compared with the {previous_label} edition, the rolling portfolio remained "
            "broadly stable: publication volume, citation visibility, access profile, and "
            "the leading journal and keyword showed no material movement.",
            False,
        )

    publication_phrase = (
        f"{abs(publication_delta)} more distinct publications"
        if publication_delta > 0
        else (
            f"{abs(publication_delta)} fewer distinct publications"
            if publication_delta < 0
            else "the same number of distinct publications"
        )
    )
    citation_phrase = (
        f"{abs(citation_delta):,} higher"
        if citation_delta > 0
        else (f"{abs(citation_delta):,} lower" if citation_delta < 0 else "unchanged")
    )
    oa_phrase = (
        f" and the open-access share moved {oa_delta_pp:+.1f} percentage points"
        if abs(oa_delta_pp) >= 0.5
        else ""
    )
    return (
        f"Compared with the {previous_label} edition, the rolling portfolio contained "
        f"{publication_phrase}; its cumulative citation total was {citation_phrase}"
        f"{oa_phrase}.",
        True,
    )


def ranking_sentence(
    subject: str,
    current_leader: str,
    current_count: int,
    current_counts: dict[str, int],
    previous_snapshot: dict | None,
    previous_key: str,
) -> str:
    if previous_snapshot is None:
        return (
            f"{current_leader} provides the initial monthly benchmark for {subject}, "
            f"with {current_count} distinct publications."
        )

    previous_counts = previous_snapshot.get(previous_key, {})
    previous_ranks = rank_map(previous_counts)
    previous_leader = next(iter(previous_ranks), None)
    if previous_leader == current_leader:
        count_delta = current_count - int(previous_counts.get(current_leader, 0))
        movement = (
            f", a change of {count_delta:+d} from the previous edition"
            if count_delta
            else ", unchanged from the previous edition"
        )
        return (
            f"{current_leader} remained the leading {subject}, with "
            f"{current_count} distinct publications{movement}."
        )

    previous_position = rank_map(current_counts).get(previous_leader)
    previous_position_text = (
        f"ranked {previous_position}" if previous_position else "left the current ranking"
    )
    return (
        f"{current_leader} became the leading {subject}, with {current_count} distinct "
        f"publications; the previous leader, {previous_leader}, {previous_position_text}."
    )


def build_artifact() -> dict:
    source_rows = pd.read_csv(PUBLICATIONS_CSV)
    with sqlite3.connect(":memory:") as connection:
        source_rows.to_sql("scopus_publications", connection, index=False)
        recent_rows = pd.read_sql_query(SOURCE_SQL, connection)

    recent_rows["Date"] = pd.to_datetime(recent_rows["Date"], errors="coerce")
    recent_rows["Citations"] = (
        pd.to_numeric(recent_rows["Citations"], errors="coerce").fillna(0).astype(int)
    )

    # The source is author-publication grain. Deduplicate by Scopus article link so
    # institutional portfolio metrics count a co-authored paper only once.
    articles = (
        recent_rows.sort_values(["Link", "Citations"], ascending=[True, False])
        .drop_duplicates(subset=["Link"])
        .copy()
    )
    articles["is_oa"] = articles["Open Access"].eq("Yes")

    publication_count = int(len(articles))
    contributor_count = int(recent_rows["Name"].nunique())
    journal_count = int(articles["Journal"].nunique())
    citation_count = int(articles["Citations"].sum())
    oa_count = int(articles["is_oa"].sum())
    oa_share = float(oa_count / publication_count) if publication_count else 0.0

    journal_stats = (
        articles.groupby("Journal", as_index=False)
        .agg(
            publications=("Link", "nunique"),
            citations=("Citations", "sum"),
            median_citations=("Citations", "median"),
            open_access_publications=("is_oa", "sum"),
        )
    )
    journal_stats["oa_share"] = (
        journal_stats["open_access_publications"] / journal_stats["publications"]
    )
    journal_stats["access_profile"] = pd.cut(
        journal_stats["oa_share"],
        bins=[-0.001, 0.2999, 0.5999, 1.0],
        labels=["Mostly closed", "Mixed access", "Open-access led"],
    ).astype(str)
    journal_stats["tier"] = journal_stats["publications"].map(portfolio_tier)

    keyword_rows = articles[["Link", "Journal", "Keywords", "Citations"]].copy()
    keyword_rows["keyword"] = (
        keyword_rows["Keywords"].fillna("").astype(str).str.split(";")
    )
    keyword_rows = keyword_rows.explode("keyword")
    keyword_rows["keyword"] = (
        keyword_rows["keyword"]
        .astype(str)
        .str.lower()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    keyword_rows = keyword_rows.loc[
        ~keyword_rows["keyword"].isin(["", "n/a", "na", "nan", "none"])
    ].drop_duplicates(["Link", "keyword"])

    keyword_stats = (
        keyword_rows.groupby("keyword", as_index=False)
        .agg(
            publications=("Link", "nunique"),
            citation_weight=("Citations", "sum"),
            journal_reach=("Journal", "nunique"),
        )
    )
    keyword_stats["citations_per_publication"] = (
        keyword_stats["citation_weight"] / keyword_stats["publications"]
    )
    keyword_stats["keyword_label"] = keyword_stats["keyword"].map(keyword_label)
    keyword_stats["tier"] = keyword_stats["publications"].map(portfolio_tier)

    keyword_journals = (
        keyword_rows.groupby(["keyword", "Journal"], as_index=False)
        .agg(publications=("Link", "nunique"))
        .sort_values(["keyword", "publications", "Journal"], ascending=[True, False, True])
    )
    top_journals_by_keyword = (
        keyword_journals.groupby("keyword")
        .head(3)
        .groupby("keyword")
        .apply(
            lambda group: "; ".join(
                f"{row.Journal} ({int(row.publications)})"
                for row in group.itertuples(index=False)
            ),
            include_groups=False,
        )
        .rename("top_journals")
        .reset_index()
    )
    keyword_stats = keyword_stats.merge(top_journals_by_keyword, on="keyword", how="left")

    journal_theme_counts = (
        keyword_rows.groupby(["Journal", "keyword"], as_index=False)
        .agg(publications=("Link", "nunique"))
        .sort_values(["Journal", "publications", "keyword"], ascending=[True, False, True])
    )
    journal_themes = (
        journal_theme_counts.groupby("Journal")
        .head(3)
        .groupby("Journal")
        .apply(
            lambda group: ", ".join(keyword_label(value) for value in group["keyword"]),
            include_groups=False,
        )
        .rename("top_themes")
        .reset_index()
    )
    journal_stats = journal_stats.merge(journal_themes, on="Journal", how="left")

    top_journals = journal_stats.sort_values(
        ["publications", "citations", "Journal"], ascending=[False, False, True]
    ).head(12)
    journal_shortlist = journal_stats.sort_values(
        ["publications", "citations", "Journal"], ascending=[False, False, True]
    ).head(30)
    journal_map = journal_stats.loc[journal_stats["publications"] >= 2].sort_values(
        ["publications", "citations"], ascending=False
    ).head(35)

    months = pd.period_range(START_DATE, END_DATE, freq="M")
    articles["month"] = articles["Date"].dt.to_period("M")
    monthly_total = articles.groupby("month").size().reindex(months, fill_value=0)
    monthly_oa = (
        articles.loc[articles["is_oa"]]
        .groupby("month")
        .size()
        .reindex(months, fill_value=0)
    )
    monthly_rows = []
    for month in months:
        month_date = month.to_timestamp().strftime("%Y-%m-%d")
        monthly_rows.extend(
            [
                {
                    "month": month_date,
                    "series": "All publications",
                    "publications": int(monthly_total.loc[month]),
                },
                {
                    "month": month_date,
                    "series": "Open access",
                    "publications": int(monthly_oa.loc[month]),
                },
            ]
        )

    top_keywords = keyword_stats.sort_values(
        ["publications", "citation_weight", "keyword_label"],
        ascending=[False, False, True],
    ).head(15)
    emerging_keywords = keyword_stats.loc[
        keyword_stats["publications"].between(3, 7)
        & (keyword_stats["citation_weight"] >= 10)
    ].sort_values(
        ["citations_per_publication", "citation_weight", "publications"],
        ascending=[False, False, False],
    ).head(15)

    journal_tiers = tier_summary(
        journal_stats,
        entity_label="Journals",
        total_associations=publication_count,
    )
    keyword_association_count = int(keyword_stats["publications"].sum())
    keyword_tiers = tier_summary(
        keyword_stats,
        entity_label="Keywords",
        total_associations=keyword_association_count,
    )
    journal_tier_lookup = journal_tiers.set_index("tier").to_dict(orient="index")
    keyword_tier_lookup = keyword_tiers.set_index("tier").to_dict(orient="index")

    top_journal_name = str(top_journals.iloc[0]["Journal"])
    top_journal_publications = int(top_journals.iloc[0]["publications"])
    top_keyword_name = str(top_keywords.iloc[0]["keyword_label"])
    top_keyword_publications = int(top_keywords.iloc[0]["publications"])
    top_ten_share = float(
        journal_stats.nlargest(10, "publications")["publications"].sum()
        / publication_count
    )
    peak_month = monthly_total.idxmax()
    peak_month_label = peak_month.to_timestamp().strftime("%B %Y")
    peak_month_publications = int(monthly_total.max())
    latest_month = END_DATE.to_period("M")
    prior_month = latest_month - 1
    latest_month_publications = int(monthly_total.loc[latest_month])
    prior_month_publications = int(monthly_total.loc[prior_month])
    preceding_six_month_average = float(
        monthly_total.loc[latest_month - 6 : latest_month - 1].mean()
    )

    journal_counts_snapshot = {
        str(row.Journal): int(row.publications)
        for row in journal_stats.itertuples(index=False)
    }
    keyword_counts_snapshot = {
        str(row.keyword_label): int(row.publications)
        for row in keyword_stats.itertuples(index=False)
    }
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    current_snapshot = {
        "period_end": END_DATE.strftime("%Y-%m-%d"),
        "generated_at": generated_at,
        "metrics": {
            "publications": publication_count,
            "contributors": contributor_count,
            "journals": journal_count,
            "citations": citation_count,
            "oa_share": round(oa_share, 6),
            "latest_month_publications": latest_month_publications,
        },
        "leaders": {
            "journal": top_journal_name,
            "keyword": top_keyword_name,
        },
        "journal_counts": journal_counts_snapshot,
        "keyword_counts": keyword_counts_snapshot,
    }
    snapshot_history = load_snapshot_history()
    previous_snapshot = previous_monthly_snapshot(snapshot_history)
    portfolio_comparison, material_monthly_change = comparison_sentence(
        current_snapshot, previous_snapshot
    )
    monthly_comparison = [
        {
            "current_period_end": current_snapshot["period_end"],
            "previous_period_end": (
                previous_snapshot["period_end"] if previous_snapshot else None
            ),
            "comparison_available": previous_snapshot is not None,
            "material_change": material_monthly_change,
            "publication_delta": (
                publication_count
                - previous_snapshot["metrics"]["publications"]
                if previous_snapshot
                else None
            ),
            "citation_delta": (
                citation_count - previous_snapshot["metrics"]["citations"]
                if previous_snapshot
                else None
            ),
            "open_access_change_pp": (
                round(
                    100
                    * (
                        oa_share
                        - previous_snapshot["metrics"]["oa_share"]
                    ),
                    2,
                )
                if previous_snapshot
                else None
            ),
        }
    ]

    activity_delta = latest_month_publications - prior_month_publications
    activity_threshold = max(3, round(prior_month_publications * 0.15))
    if activity_delta >= activity_threshold:
        activity_comparison = (
            f"Output increased from {prior_month_publications} publications in "
            f"{prior_month.to_timestamp():%B %Y} to {latest_month_publications} in "
            f"{latest_month.to_timestamp():%B %Y}."
        )
    elif activity_delta <= -activity_threshold:
        activity_comparison = (
            f"Output declined from {prior_month_publications} publications in "
            f"{prior_month.to_timestamp():%B %Y} to {latest_month_publications} in "
            f"{latest_month.to_timestamp():%B %Y}."
        )
    else:
        activity_comparison = (
            f"Output was broadly stable, moving from {prior_month_publications} publications "
            f"in {prior_month.to_timestamp():%B %Y} to {latest_month_publications} in "
            f"{latest_month.to_timestamp():%B %Y}."
        )

    if preceding_six_month_average:
        average_difference = (
            latest_month_publications / preceding_six_month_average - 1
        )
        if abs(average_difference) >= 0.1:
            average_comparison = (
                f"The latest month was {abs(average_difference):.0%} "
                f"{'above' if average_difference > 0 else 'below'} the preceding "
                f"six-month average of {preceding_six_month_average:.1f} publications."
            )
        else:
            average_comparison = (
                f"The latest month remained close to the preceding six-month average of "
                f"{preceding_six_month_average:.1f} publications."
            )
    else:
        average_comparison = ""

    journal_change_commentary = ranking_sentence(
        "journal",
        top_journal_name,
        top_journal_publications,
        journal_counts_snapshot,
        previous_snapshot,
        "journal_counts",
    )
    keyword_change_commentary = ranking_sentence(
        "research theme",
        top_keyword_name,
        top_keyword_publications,
        keyword_counts_snapshot,
        previous_snapshot,
        "keyword_counts",
    )

    keyword_movement_commentary = (
        "This edition establishes the initial benchmark for monitoring changes in thematic prominence."
    )
    if previous_snapshot is not None:
        previous_keyword_counts = previous_snapshot.get("keyword_counts", {})
        keyword_deltas = sorted(
            (
                (
                    keyword,
                    count - int(previous_keyword_counts.get(keyword, 0)),
                    count,
                )
                for keyword, count in keyword_counts_snapshot.items()
            ),
            key=lambda item: (-item[1], -item[2], item[0].lower()),
        )
        positive_movers = [item for item in keyword_deltas if item[1] >= 2]
        if positive_movers:
            mover, increase, mover_count = positive_movers[0]
            keyword_movement_commentary = (
                f"{mover} recorded the largest increase in thematic coverage, rising by "
                f"{increase} publications to {mover_count} in the rolling window."
            )
        else:
            keyword_movement_commentary = (
                "No normalized keyword increased by two or more publications relative to "
                "the preceding edition, indicating a broadly stable thematic profile."
            )

    journal_tier_commentary = tier_movement_sentence(
        journal_counts_snapshot,
        previous_snapshot,
        "journal_counts",
        "journals",
    )
    keyword_tier_commentary = tier_movement_sentence(
        keyword_counts_snapshot,
        previous_snapshot,
        "keyword_counts",
        "keywords",
    )

    previous_journal_counts = (
        previous_snapshot.get("journal_counts", {}) if previous_snapshot else {}
    )
    journal_signal_rows = pd.concat(
        [
            journal_stats.loc[journal_stats["tier"] == "Developing (2–4)"]
            .sort_values(
                ["publications", "citations", "Journal"],
                ascending=[False, False, True],
            )
            .head(40),
            journal_stats.loc[journal_stats["tier"] == "Exploratory (1)"]
            .sort_values(["citations", "Journal"], ascending=[False, True])
            .head(20),
        ],
        ignore_index=True,
    )
    journal_signal_rows["movement"] = journal_signal_rows.apply(
        lambda row: movement_label(
            int(row["publications"]),
            (
                int(previous_journal_counts.get(str(row["Journal"]), 0))
                if previous_snapshot
                else None
            ),
        ),
        axis=1,
    )

    previous_keyword_counts = (
        previous_snapshot.get("keyword_counts", {}) if previous_snapshot else {}
    )
    keyword_signal_rows = pd.concat(
        [
            keyword_stats.loc[keyword_stats["tier"] == "Developing (2–4)"]
            .sort_values(
                ["publications", "citation_weight", "keyword_label"],
                ascending=[False, False, True],
            )
            .head(40),
            keyword_stats.loc[keyword_stats["tier"] == "Exploratory (1)"]
            .sort_values(
                ["citation_weight", "keyword_label"],
                ascending=[False, True],
            )
            .head(20),
        ],
        ignore_index=True,
    )
    keyword_signal_rows["movement"] = keyword_signal_rows.apply(
        lambda row: movement_label(
            int(row["publications"]),
            (
                int(previous_keyword_counts.get(str(row["keyword_label"]), 0))
                if previous_snapshot
                else None
            ),
        ),
        axis=1,
    )

    summary = pd.DataFrame(
        [
            {
                "distinct_publications": publication_count,
                "contributors": contributor_count,
                "journals": journal_count,
                "citations": citation_count,
                "open_access_share": oa_share,
            }
        ]
    )

    top_journals_dataset = top_journals[
        [
            "Journal",
            "publications",
            "citations",
            "oa_share",
            "open_access_publications",
            "top_themes",
        ]
    ].rename(columns={"Journal": "journal"})

    journal_map_dataset = journal_map[
        [
            "Journal",
            "publications",
            "citations",
            "oa_share",
            "access_profile",
            "top_themes",
        ]
    ].rename(columns={"Journal": "journal"})

    journal_shortlist_dataset = journal_shortlist[
        [
            "Journal",
            "publications",
            "citations",
            "median_citations",
            "oa_share",
            "top_themes",
        ]
    ].rename(columns={"Journal": "journal"})
    journal_shortlist_dataset["median_citations"] = journal_shortlist_dataset[
        "median_citations"
    ].round(1)

    top_keywords_dataset = top_keywords[
        [
            "keyword_label",
            "publications",
            "citation_weight",
            "citations_per_publication",
            "journal_reach",
            "top_journals",
        ]
    ].rename(columns={"keyword_label": "keyword"})
    top_keywords_dataset["citations_per_publication"] = top_keywords_dataset[
        "citations_per_publication"
    ].round(1)

    emerging_dataset = emerging_keywords[
        [
            "keyword_label",
            "publications",
            "citation_weight",
            "citations_per_publication",
            "journal_reach",
            "top_journals",
        ]
    ].rename(columns={"keyword_label": "keyword"})
    emerging_dataset["citations_per_publication"] = emerging_dataset[
        "citations_per_publication"
    ].round(1)

    journal_tiers_dataset = journal_tiers[
        ["tier", "entities", "publication_associations", "portfolio_share"]
    ].rename(
        columns={
            "entities": "journals",
            "publication_associations": "publications",
        }
    )
    keyword_tiers_dataset = keyword_tiers[
        ["tier", "entities", "publication_associations", "portfolio_share"]
    ].rename(
        columns={
            "entities": "keywords",
            "publication_associations": "publication_keyword_associations",
        }
    )
    journal_signals_dataset = journal_signal_rows[
        [
            "Journal",
            "tier",
            "publications",
            "citations",
            "median_citations",
            "oa_share",
            "movement",
            "top_themes",
        ]
    ].rename(columns={"Journal": "journal"})
    journal_signals_dataset["median_citations"] = journal_signals_dataset[
        "median_citations"
    ].round(1)

    keyword_signals_dataset = keyword_signal_rows[
        [
            "keyword_label",
            "tier",
            "publications",
            "citation_weight",
            "citations_per_publication",
            "journal_reach",
            "movement",
            "top_journals",
        ]
    ].rename(columns={"keyword_label": "keyword"})
    keyword_signals_dataset["citations_per_publication"] = keyword_signals_dataset[
        "citations_per_publication"
    ].round(1)

    publication_date = datetime.now().astimezone().strftime("%-d %B %Y")
    publication_year = datetime.now().astimezone().year
    report_title = "PPTI Research Publication Trends and Strategic Positioning"
    reporting_window_short = (
        f"{START_DATE:%B %Y}–{END_DATE:%B %Y}"
    )
    reporting_window_long = (
        f"{START_DATE.day} {START_DATE:%B %Y} to "
        f"{END_DATE.day} {END_DATE:%B %Y}"
    )
    journal_long_tail_entities = int(
        journal_tier_lookup["Developing (2–4)"]["entities"]
        + journal_tier_lookup["Exploratory (1)"]["entities"]
    )
    journal_long_tail_publications = int(
        journal_tier_lookup["Developing (2–4)"]["publication_associations"]
        + journal_tier_lookup["Exploratory (1)"]["publication_associations"]
    )
    journal_long_tail_share = (
        journal_long_tail_publications / publication_count if publication_count else 0.0
    )
    keyword_long_tail_entities = int(
        keyword_tier_lookup["Developing (2–4)"]["entities"]
        + keyword_tier_lookup["Exploratory (1)"]["entities"]
    )
    keyword_long_tail_associations = int(
        keyword_tier_lookup["Developing (2–4)"]["publication_associations"]
        + keyword_tier_lookup["Exploratory (1)"]["publication_associations"]
    )
    keyword_long_tail_share = (
        keyword_long_tail_associations / keyword_association_count
        if keyword_association_count
        else 0.0
    )
    source = {
        "id": SOURCE_ID,
        "label": "Scopus publication snapshot",
        "path": "data/ppti_journal_publications.csv",
        "query": {
            "engine": "SQLite",
            "language": "sql",
            "sql": SOURCE_SQL,
            "description": (
                "Loads Scopus-derived publication records within the last 24 completed months. "
                "The report builder deduplicates articles by Scopus link and compares the "
                "current edition with the preceding monthly snapshot when available."
            ),
            "executed_at": generated_at,
            "tables_used": ["scopus_publications"],
            "filters": [
                f"Publication date from {reporting_window_long}",
                "One institutional publication per distinct Scopus article link after extraction",
                "Blank and sentinel author keywords excluded from keyword summaries",
                f"Monthly comparison methodology: {METHODOLOGY_VERSION}",
            ],
            "metric_definitions": [
                "Distinct publications: unique Scopus article links in the reporting window.",
                "Contributors: unique PPTI researcher names attached to in-window records.",
                "Citation count: sum of current Scopus citations across distinct publications.",
                "Open-access share: distinct publications marked Open Access = Yes divided by distinct publications.",
                "Keyword publications: distinct publications carrying the normalized author keyword.",
            ],
        },
    }

    abstract = (
        "## Abstract\n\n"
        f"This report examines {publication_count} distinct Scopus-indexed publications "
        f"associated with {contributor_count} PPTI researchers during the 24-month period "
        f"from {reporting_window_long}. The publications appeared in {journal_count} "
        f"journals and had received {citation_count:,} citations at the time of data collection. "
        f"The analysis considers publication activity, journal concentration, citation visibility, "
        f"open-access status, and recurring author keywords to describe the institution's recent "
        f"research profile. The results show a broad journal portfolio, with the ten most frequently "
        f"used titles accounting for {top_ten_share:.0%} of publications. {top_journal_name} "
        f"was the most frequently used journal, with {top_journal_publications} articles, while "
        f"{top_keyword_name} was the most prevalent normalized author keyword, appearing in "
        f"{top_keyword_publications} publications. Open-access articles represented {oa_share:.0%} "
        f"of the portfolio. Journals below the established tier accounted for "
        f"{journal_long_tail_share:.0%} of publications, while developing and exploratory "
        f"keywords accounted for {keyword_long_tail_share:.0%} of publication–keyword associations. "
        f"{portfolio_comparison} Together, these findings provide an evidence "
        "base for understanding "
        "PPTI's publication patterns and for aligning future manuscripts with demonstrated areas "
        "of research activity and journal experience."
    )

    blocks = [
        {
            "id": "title",
            "type": "markdown",
            "body": (
                f"# {report_title}\n\n"
                f"### A bibliometric report covering {reporting_window_short}\n\n"
                "**Yusri Yusup · Wan Zafira Wan Zakaria · Lee Chee Keong · "
                "Norli Ismail · Abdorezza Mohammad Nafchi · Mohd Nurazzi Norizan**\n\n"
                "Research Trends Committee 2025  \n"
                "School of Industrial Technology  \n"
                "Universiti Sains Malaysia\n\n"
                f"Published {publication_date}"
            ),
        },
        {
            "id": "abstract",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": abstract,
        },
        {
            "id": "portfolio_metrics",
            "type": "metric-strip",
            "cardIds": [
                "distinct_publications",
                "contributors",
                "journal_reach",
                "citation_visibility",
                "open_access_share",
            ],
        },
        {
            "id": "definitions",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "## 1. Scope and method\n\n"
                "The report is based on Scopus publication records associated with PPTI researchers "
                f"and dated between {reporting_window_long}. The unit of analysis is a distinct "
                "Scopus-indexed publication rather than an author–publication record; consequently, "
                "a publication co-authored by more than one PPTI researcher is counted once in the "
                "portfolio totals. Citation values represent the cumulative Scopus counts available "
                "when the dataset was collected, and thematic analysis is based on normalized author "
                "keywords. Monthly commentary is generated by comparing the current metrics and "
                "rankings with the preceding saved edition under the same methodology. Journal and "
                "keyword frequencies therefore describe both the present portfolio and any material "
                "movement since the previous report."
            ),
        },
        {
            "id": "activity_story",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "## 2. Publication activity\n\n"
                f"{activity_comparison} {average_comparison} Across the full reporting period, "
                f"monthly output reached its highest level in {peak_month_label}, when "
                f"{peak_month_publications} distinct publications were recorded. The open-access "
                "series is a subset of total output and shows the portion of monthly publications "
                "available through an open-access route."
            ),
        },
        {"id": "monthly_activity", "type": "chart", "chartId": "monthly_activity_chart", "layout": "full"},
        {
            "id": "journal_story",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "## 3. Journal distribution\n\n"
                f"{journal_change_commentary} The institution's output was "
                f"distributed across {journal_count} journals, and the ten most frequently used titles "
                f"accounted for {top_ten_share:.0%} of the portfolio. The distribution demonstrates both "
                "repeated experience with a core group of outlets and substantial disciplinary breadth. "
                "Journal frequency is interpreted here as evidence of prior institutional presence and "
                "thematic alignment rather than as a measure of journal quality or future acceptance."
            ),
        },
        {"id": "top_journals", "type": "chart", "chartId": "top_journals_chart", "layout": "full"},
        {
            "id": "journal_tier_story",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "### 3.1 Most publication activity sits beyond the established journal group\n\n"
                f"The developing and exploratory tiers contain {journal_long_tail_entities} journals "
                f"and account for {journal_long_tail_publications} publications, or "
                f"{journal_long_tail_share:.0%} of the portfolio. The tier view therefore complements "
                "the leading-journal ranking by distinguishing repeated institutional experience from "
                "new and specialist outlet use. "
                f"{journal_tier_commentary}"
            ),
        },
        {
            "id": "journal_tiers",
            "type": "table",
            "tableId": "journal_tiers_table",
            "layout": "full",
        },
        {
            "id": "journal_signals_story",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "### 3.2 Developing and exploratory journal signals\n\n"
                "The signal table examines the middle and lower publication tiers directly. It includes "
                "the forty strongest developing outlets by publication and citation evidence together "
                "with the twenty exploratory outlets showing the greatest current citation visibility. "
                "Movement records whether an outlet is new, stable, or changing relative to the preceding "
                "monthly edition. Citation evidence is interpreted within each tier so that a single highly "
                "cited article does not place an exploratory outlet on the same footing as a repeatedly used journal."
            ),
        },
        {
            "id": "journal_signals",
            "type": "table",
            "tableId": "journal_signals_table",
            "layout": "full",
        },
        {
            "id": "journal_map_story",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "### 3.3 Publication frequency and citation visibility\n\n"
                "The journal map relates the number of distinct publications in each outlet to their cumulative "
                "citation totals. Journals positioned toward the upper right combine repeated PPTI publication "
                "activity with comparatively strong citation visibility. In contrast, a journal with high citation "
                "counts but few publications may reflect the influence of a small number of articles. The two "
                "measures are therefore presented as complementary dimensions of the portfolio: frequency "
                "indicates the extent of recent use, whereas citations indicate the visibility accumulated by "
                "the articles published in that outlet."
            ),
        },
        {"id": "journal_map", "type": "chart", "chartId": "journal_map_chart", "layout": "full"},
        {
            "id": "shortlist_story",
            "type": "markdown",
            "body": (
                "### 3.4 Journal-level evidence\n\n"
                "The journal-level table provides the underlying values used to compare frequently selected "
                "outlets. It reports publication volume, cumulative and median citation counts, open-access "
                "share, and the three most frequent normalized author keywords associated with each journal. "
                "Read together, these fields describe the scale, visibility, access profile, and thematic "
                "character of PPTI's recent presence in each outlet."
            ),
        },
        {"id": "journal_shortlist", "type": "table", "tableId": "journal_shortlist_table", "layout": "full"},
        {
            "id": "theme_story",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "## 4. Research themes\n\n"
                f"{keyword_change_commentary} The wider ranking "
                "identifies subjects that recur across the portfolio and consequently provides a consolidated "
                "view of PPTI's established research profile. The frequency of a keyword reflects repeated use "
                "across publications, while its journal reach indicates the breadth of outlets through which "
                "the theme has been disseminated."
            ),
        },
        {"id": "top_keywords", "type": "chart", "chartId": "top_keywords_chart", "layout": "full"},
        {
            "id": "keyword_tier_story",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "### 4.1 The keyword portfolio is even more strongly long-tailed\n\n"
                f"The developing and exploratory tiers contain {keyword_long_tail_entities} normalized "
                f"keywords and account for {keyword_long_tail_associations} publication–keyword associations, "
                f"or {keyword_long_tail_share:.0%} of all associations. This tiering prevents established "
                "themes from obscuring narrower or newly appearing subjects while preserving the distinction "
                "between repeated and single-publication evidence. "
                f"{keyword_tier_commentary}"
            ),
        },
        {
            "id": "keyword_tiers",
            "type": "table",
            "tableId": "keyword_tiers_table",
            "layout": "full",
        },
        {
            "id": "keyword_signals_story",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "### 4.2 Developing and exploratory keyword signals\n\n"
                "The keyword signal table reviews the forty developing themes with the strongest publication "
                "and citation evidence and the twenty exploratory themes with the greatest citation-weighted "
                "visibility. Journal reach and associated outlets provide context for whether a theme is "
                "spreading across the portfolio or remains concentrated in a single publication. "
                f"{keyword_movement_commentary}"
            ),
        },
        {
            "id": "keyword_signals",
            "type": "table",
            "tableId": "keyword_signals_table",
            "layout": "full",
        },
        {
            "id": "emerging_story",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "### 4.3 Citation-visible emerging themes\n\n"
                "Emerging thematic signals are defined in this report as normalized keywords associated with "
                "three to seven distinct publications and at least ten citation-weighted mentions. These themes "
                "have a smaller publication base than the established topics shown above but have already achieved "
                f"measurable citation visibility. {keyword_movement_commentary} The table records their "
                "publication frequency, citation-weighted "
                "mentions, citations per publication, journal reach, and most closely associated journals."
            ),
        },
        {"id": "emerging_keywords", "type": "table", "tableId": "emerging_keywords_table", "layout": "full"},
        {
            "id": "conclusion",
            "type": "markdown",
            "sourceId": SOURCE_ID,
            "body": (
                "## 5. Conclusion\n\n"
                f"PPTI's recent Scopus-indexed output is characterized by sustained publication activity, "
                f"a broad journal base, and identifiable concentrations of research expertise. Across "
                f"{publication_count} distinct publications, the portfolio combines repeated use of established "
                f"outlets with dissemination across {journal_count} journals, while {oa_share:.0%} of articles "
                f"were available through open access. The tier analysis shows that "
                f"{journal_long_tail_share:.0%} of publications occurred in developing or exploratory "
                f"journals and that {keyword_long_tail_share:.0%} of publication–keyword associations "
                f"sat outside the established keyword tier. {portfolio_comparison} The journal and keyword results "
                "together document the "
                "institution's recent publication position and provide a reproducible baseline against which "
                "subsequent reporting periods can be compared."
            ),
        },
        {
            "id": "citation",
            "type": "markdown",
            "body": (
                "## Suggested citation\n\n"
                f"Yusup, Y., Wan Zakaria, W. Z., Lee, C. K., Ismail, N., Mohammad Nafchi, A., "
                f"& Norizan, M. N. ({publication_year}). *{report_title}: A bibliometric report "
                f"covering {reporting_window_short}*. Research Trends Committee 2025, School of "
                "Industrial Technology, Universiti Sains Malaysia."
            ),
        },
    ]

    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": report_title,
            "description": (
                "A bibliometric report on recent PPTI publication activity, journal distribution, "
                "citation visibility, open-access status, and research themes."
            ),
            "generatedAt": generated_at,
            "cards": [
                {
                    "id": "distinct_publications",
                    "description": "Unique Scopus articles in the reporting window.",
                    "dataset": "summary",
                    "sourceId": SOURCE_ID,
                    "metrics": [
                        {
                            "label": "Distinct publications",
                            "field": "distinct_publications",
                            "format": "number",
                        }
                    ],
                },
                {
                    "id": "contributors",
                    "description": "PPTI researchers linked to at least one in-window article.",
                    "dataset": "summary",
                    "sourceId": SOURCE_ID,
                    "metrics": [
                        {"label": "Active contributors", "field": "contributors", "format": "number"}
                    ],
                },
                {
                    "id": "journal_reach",
                    "description": "Distinct journals represented by recent articles.",
                    "dataset": "summary",
                    "sourceId": SOURCE_ID,
                    "metrics": [{"label": "Journal reach", "field": "journals", "format": "number"}],
                },
                {
                    "id": "citation_visibility",
                    "description": "Current cumulative citations across distinct articles.",
                    "dataset": "summary",
                    "sourceId": SOURCE_ID,
                    "metrics": [{"label": "Current citations", "field": "citations", "format": "compact"}],
                },
                {
                    "id": "open_access_share",
                    "description": "Share of distinct articles marked open access by Scopus.",
                    "dataset": "summary",
                    "sourceId": SOURCE_ID,
                    "metrics": [
                        {
                            "label": "Open-access share",
                            "field": "open_access_share",
                            "format": "percent",
                        }
                    ],
                },
            ],
            "charts": [
                {
                    "id": "monthly_activity_chart",
                    "title": "Monthly publication activity",
                    "subtitle": (
                        f"Distinct Scopus articles, {reporting_window_short}; "
                        "open access is a subset of total output."
                    ),
                    "showDescription": True,
                    "intent": "trend",
                    "question": "How consistently has PPTI published across the reporting window?",
                    "type": "line",
                    "dataset": "monthly_activity",
                    "sourceId": SOURCE_ID,
                    "encodings": {
                        "x": {"field": "month", "type": "temporal", "label": "Month"},
                        "y": {
                            "field": "publications",
                            "type": "quantitative",
                            "label": "Distinct publications",
                            "format": "number",
                        },
                        "color": {"field": "series", "type": "nominal", "label": "Series"},
                        "tooltip": [
                            {"field": "month", "type": "temporal", "label": "Month"},
                            {
                                "field": "publications",
                                "type": "quantitative",
                                "label": "Publications",
                                "format": "number",
                            },
                        ],
                    },
                    "yAxisTitle": "Distinct publications",
                    "valueFormat": "number",
                    "palette": {"kind": "categorical"},
                    "legend": {"position": "bottom", "interactive": True, "sort": "spec"},
                    "labels": {"values": "endpoints"},
                    "layout": "full",
                },
                {
                    "id": "top_journals_chart",
                    "title": "Most frequently used journals",
                    "subtitle": (
                        f"Top 12 journals by distinct PPTI publications, "
                        f"{reporting_window_short}."
                    ),
                    "showDescription": True,
                    "intent": "comparison",
                    "question": "Which journals have the strongest repeat-publication evidence?",
                    "type": "horizontalBar",
                    "dataset": "top_journals",
                    "sourceId": SOURCE_ID,
                    "encodings": {
                        "x": {"field": "journal", "type": "nominal", "label": "Journal"},
                        "y": {
                            "field": "publications",
                            "type": "quantitative",
                            "label": "Distinct publications",
                            "format": "number",
                        },
                        "tooltip": [
                            {"field": "citations", "type": "quantitative", "label": "Citations"},
                            {"field": "oa_share", "type": "quantitative", "label": "Open-access share", "format": "percent"},
                            {"field": "top_themes", "type": "text", "label": "Top themes"},
                        ],
                    },
                    "yAxisTitle": "Distinct publications",
                    "valueFormat": "number",
                    "palette": {"kind": "identity"},
                    "labels": {"values": "all"},
                    "settings": {"sort": "descending", "showValues": True, "categoryLabelPolicy": "wrap"},
                    "layout": "full",
                },
                {
                    "id": "journal_map_chart",
                    "title": "Journal publication and citation map",
                    "subtitle": "Journals with at least two distinct PPTI articles; citations are current cumulative counts.",
                    "showDescription": True,
                    "intent": "relationship",
                    "question": "Which journals combine repeat publication activity with citation visibility?",
                    "type": "scatter",
                    "dataset": "journal_map",
                    "sourceId": SOURCE_ID,
                    "encodings": {
                        "x": {
                            "field": "publications",
                            "type": "quantitative",
                            "label": "Distinct publications",
                            "format": "number",
                        },
                        "y": {
                            "field": "citations",
                            "type": "quantitative",
                            "label": "Current citations",
                            "format": "number",
                        },
                        "color": {
                            "field": "access_profile",
                            "type": "nominal",
                            "label": "Access profile",
                        },
                        "label": {"field": "journal", "type": "text", "label": "Journal"},
                        "tooltip": [
                            {"field": "journal", "type": "text", "label": "Journal"},
                            {"field": "oa_share", "type": "quantitative", "label": "Open-access share", "format": "percent"},
                            {"field": "top_themes", "type": "text", "label": "Top themes"},
                        ],
                    },
                    "xAxisTitle": "Distinct publications",
                    "yAxisTitle": "Current citations",
                    "valueFormat": "number",
                    "palette": {"kind": "categorical"},
                    "legend": {"position": "bottom", "interactive": True, "sort": "labelAsc"},
                    "layout": "full",
                },
                {
                    "id": "top_keywords_chart",
                    "title": "Most frequent author keywords",
                    "subtitle": (
                        f"Top 15 normalized keywords by distinct publications, "
                        f"{reporting_window_short}."
                    ),
                    "showDescription": True,
                    "intent": "comparison",
                    "question": "Which research themes have the strongest repeated publication footprint?",
                    "type": "horizontalBar",
                    "dataset": "top_keywords",
                    "sourceId": SOURCE_ID,
                    "encodings": {
                        "x": {"field": "keyword", "type": "nominal", "label": "Keyword"},
                        "y": {
                            "field": "publications",
                            "type": "quantitative",
                            "label": "Distinct publications",
                            "format": "number",
                        },
                        "tooltip": [
                            {"field": "citation_weight", "type": "quantitative", "label": "Citation-weighted mentions"},
                            {"field": "journal_reach", "type": "quantitative", "label": "Journal reach"},
                            {"field": "top_journals", "type": "text", "label": "Top journals"},
                        ],
                    },
                    "yAxisTitle": "Distinct publications",
                    "valueFormat": "number",
                    "palette": {"kind": "identity"},
                    "labels": {"values": "all"},
                    "settings": {"sort": "descending", "showValues": True, "categoryLabelPolicy": "wrap"},
                    "layout": "full",
                },
            ],
            "tables": [
                {
                    "id": "journal_tiers_table",
                    "title": "Journal portfolio tiers",
                    "subtitle": (
                        f"All {journal_count} journals grouped by distinct publication count, "
                        f"{reporting_window_short}."
                    ),
                    "dataset": "journal_tiers",
                    "sourceId": SOURCE_ID,
                    "density": "spacious",
                    "layout": "full",
                    "columns": [
                        {"field": "tier", "label": "Journal tier", "type": "text"},
                        {"field": "journals", "label": "Journals", "format": "number"},
                        {"field": "publications", "label": "Publications", "format": "number"},
                        {"field": "portfolio_share", "label": "Portfolio share", "format": "percent"},
                    ],
                },
                {
                    "id": "journal_signals_table",
                    "title": "Developing and exploratory journal signals",
                    "subtitle": (
                        "Forty developing outlets and twenty citation-visible exploratory outlets; "
                        "movement is relative to the preceding monthly edition."
                    ),
                    "dataset": "journal_signals",
                    "sourceId": SOURCE_ID,
                    "defaultSort": {"field": "publications", "direction": "desc"},
                    "density": "dense",
                    "layout": "full",
                    "columns": [
                        {"field": "journal", "label": "Journal", "type": "text"},
                        {"field": "tier", "label": "Tier", "type": "text"},
                        {"field": "publications", "label": "Publications", "format": "number"},
                        {"field": "citations", "label": "Citations", "format": "number"},
                        {"field": "median_citations", "label": "Median citations/article", "format": "number"},
                        {"field": "oa_share", "label": "Open-access share", "format": "percent"},
                        {"field": "movement", "label": "Monthly movement", "type": "text"},
                        {"field": "top_themes", "label": "Frequent themes", "type": "text"},
                    ],
                },
                {
                    "id": "journal_shortlist_table",
                    "title": "Journal evidence shortlist",
                    "subtitle": "Top 30 journals ranked by distinct PPTI publications in the reporting window.",
                    "dataset": "journal_shortlist",
                    "sourceId": SOURCE_ID,
                    "defaultSort": {"field": "publications", "direction": "desc"},
                    "density": "spacious",
                    "layout": "full",
                    "columns": [
                        {"field": "journal", "label": "Journal", "type": "text"},
                        {"field": "publications", "label": "Publications", "format": "number"},
                        {"field": "citations", "label": "Citations", "format": "number"},
                        {"field": "median_citations", "label": "Median citations/article", "format": "number"},
                        {"field": "oa_share", "label": "Open-access share", "format": "percent"},
                        {"field": "top_themes", "label": "Frequent themes", "type": "text"},
                    ],
                },
                {
                    "id": "keyword_tiers_table",
                    "title": "Keyword portfolio tiers",
                    "subtitle": (
                        f"All normalized author keywords grouped by distinct publication count, "
                        f"{reporting_window_short}."
                    ),
                    "dataset": "keyword_tiers",
                    "sourceId": SOURCE_ID,
                    "density": "spacious",
                    "layout": "full",
                    "columns": [
                        {"field": "tier", "label": "Keyword tier", "type": "text"},
                        {"field": "keywords", "label": "Keywords", "format": "number"},
                        {
                            "field": "publication_keyword_associations",
                            "label": "Publication–keyword associations",
                            "format": "number",
                        },
                        {"field": "portfolio_share", "label": "Association share", "format": "percent"},
                    ],
                },
                {
                    "id": "keyword_signals_table",
                    "title": "Developing and exploratory keyword signals",
                    "subtitle": (
                        "Forty developing themes and twenty citation-visible exploratory themes; "
                        "movement is relative to the preceding monthly edition."
                    ),
                    "dataset": "keyword_signals",
                    "sourceId": SOURCE_ID,
                    "defaultSort": {"field": "publications", "direction": "desc"},
                    "density": "dense",
                    "layout": "full",
                    "columns": [
                        {"field": "keyword", "label": "Keyword", "type": "text"},
                        {"field": "tier", "label": "Tier", "type": "text"},
                        {"field": "publications", "label": "Publications", "format": "number"},
                        {"field": "citation_weight", "label": "Citation-weighted mentions", "format": "number"},
                        {"field": "citations_per_publication", "label": "Citations/publication", "format": "number"},
                        {"field": "journal_reach", "label": "Journal reach", "format": "number"},
                        {"field": "movement", "label": "Monthly movement", "type": "text"},
                        {"field": "top_journals", "label": "Most associated journals", "type": "text"},
                    ],
                },
                {
                    "id": "emerging_keywords_table",
                    "title": "Emerging keyword signals",
                    "subtitle": "Keywords appearing in 3–7 distinct publications with at least 10 citation-weighted mentions.",
                    "dataset": "emerging_keywords",
                    "sourceId": SOURCE_ID,
                    "defaultSort": {"field": "citations_per_publication", "direction": "desc"},
                    "density": "spacious",
                    "layout": "full",
                    "columns": [
                        {"field": "keyword", "label": "Keyword", "type": "text"},
                        {"field": "publications", "label": "Publications", "format": "number"},
                        {"field": "citation_weight", "label": "Citation-weighted mentions", "format": "number"},
                        {"field": "citations_per_publication", "label": "Citations/publication", "format": "number"},
                        {"field": "journal_reach", "label": "Journal reach", "format": "number"},
                        {"field": "top_journals", "label": "Most associated journals", "type": "text"},
                    ],
                },
            ],
            "sources": [
                {
                    "id": SOURCE_ID,
                    "label": "Scopus publication snapshot",
                    "path": "data/ppti_journal_publications.csv",
                }
            ],
            "blocks": blocks,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "summary": records(summary),
                "monthly_activity": monthly_rows,
                "top_journals": records(top_journals_dataset),
                "journal_tiers": records(journal_tiers_dataset),
                "journal_signals": records(journal_signals_dataset),
                "journal_map": records(journal_map_dataset),
                "journal_shortlist": records(journal_shortlist_dataset),
                "top_keywords": records(top_keywords_dataset),
                "keyword_tiers": records(keyword_tiers_dataset),
                "keyword_signals": records(keyword_signals_dataset),
                "emerging_keywords": records(emerging_dataset),
                "monthly_comparison": monthly_comparison,
            },
        },
        "sources": [source],
        "package_info": {
            "artifact": "monthly production report",
            "narrativeWorkflow": "month-aware comparison enabled",
            "comparisonBaseline": (
                previous_snapshot["period_end"]
                if previous_snapshot
                else "initial baseline"
            ),
        },
    }

    save_monthly_snapshot(current_snapshot)
    print(
        "Built publication strategy evidence: "
        f"{publication_count} distinct publications, {contributor_count} contributors, "
        f"{journal_count} journals, {compact_number(citation_count)} citations, "
        f"{oa_share:.1%} open access."
    )
    return artifact


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = OUTPUT_DIR / "artifact.json"
    artifact_path.write_text(
        json.dumps(build_artifact(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {artifact_path}")

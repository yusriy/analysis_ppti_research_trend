# PPTI Research Publication Trends and Strategic Positioning — build notes

## Reporting job

- Audience: School leadership, Research Trends Committee, and PPTI researchers.
- Decision: Identify demonstrated journal fit and thematic momentum to support publication planning.
- Window: The last 24 completed months, recalculated at build time.
- Unit: One distinct publication per Scopus article link; author–article rows are retained only for contributor counts.
- Delivery: Portable HTML generated from the canonical report artifact.
- Production output: `ppti_publication_strategy_report.html` in the repository root,
  beside the journal and keyword reports.
- Automation: `scripts/build_publication_strategy_report.sh`, called by the same
  monthly runner as the two existing reports.

## Audio companion

After the analytical artifact and portable report are built, the monthly runner
calls `scripts/build_podcast.sh`. The podcast generator reads the artifact as its
sole evidence source, selects a journal and keyword represented in the latest
completed month, and ranks them by repeated support across the rolling window,
and produces a short PPTI Research Brief. The current MP3, synchronized captions,
vertical social video, transcript, cover, social copy, and metadata are stored in
`podcast/latest/`; the preceding edition is archived under `podcast/archive/YYYY-MM/`.

The ElevenLabs API key and voice ID are read from the Git-ignored
`.secrets/elevenlabs.env` file. The API key is never written to an output file.
Repeated builds reuse matching narration rather than spending credits again.

## Monthly narrative workflow

The builder stores a compact comparison baseline in `monthly_snapshots.json`. Each
snapshot contains portfolio metrics, leading journals, and normalized keyword counts,
but not a duplicate copy of the publication CSV. A maximum of 13 monthly snapshots is
retained.

Before a newer completed-month edition replaces the current reports,
`scripts/archive_reports.py` copies all three HTML reports into
`archive/YYYY-MM/`. The publication strategy report's canonical artifact is archived
with its HTML output for reproducibility.

At each build, the report:

1. Recalculates the rolling 24-month reporting window.
2. Deduplicates author–publication rows by Scopus article link.
3. Compares the current edition with the immediately preceding saved monthly snapshot
   produced under the same methodology.
4. Measures publication, citation, open-access, journal-ranking, and keyword movement.
5. Classifies journals and normalized keywords as established (5+ publications),
   developing (2–4), or exploratory (1), then detects movement between tiers.
6. Selects narrative language according to materiality thresholds.
7. Uses a first-edition baseline statement when no comparable earlier snapshot exists.
8. Replaces the current-period snapshot without removing the preceding period, so
   repeated builds during the same month preserve the correct comparison baseline.

Material change is defined as at least three publications, 25 cumulative citations,
two percentage points of open-access share, or a change in the leading journal or
keyword. Monthly activity is also compared with the previous month and the preceding
six-month average. Keyword movement is highlighted when a normalized keyword gains
at least two publications.

## Middle- and long-tail coverage

The report evaluates every journal and normalized keyword before selecting display
rows. Tier summary tables account for the complete portfolio. Signal tables then show:

- the forty developing journals and keywords with the strongest publication and
  citation evidence; and
- the twenty exploratory journals and keywords with the greatest current
  citation visibility.

The signal tables are intentionally tier-aware. A single-publication journal or keyword
is not ranked as equivalent to an entity supported by repeated publications. Monthly
movement labels identify new entries, increases, decreases, and stable signals.

The podcast's "This Month's Signals" panel is selected separately from those tier
tables. Candidates must occur in the latest completed month. The top three journals
are ranked by latest-month publications, rolling-window publications, and citations.
The top three themes use the same recency and repeated-support principle; unsupported
one-word fragments are excluded from the featured slots.

Tier summaries use tables rather than charts because each has only three categories
and exact counts and shares are the primary reading task. This avoids underpowered
three-bar visuals while keeping the quantitative comparison explicit.

## Required executive structure mapping

- Title: `PPTI Research Publication Trends and Strategic Positioning`
- Abstract: first narrative block after the title
- Key findings with evidence: activity, journal shortlist/map, and theme sections
- Recommended next steps: omitted at the user's request because this is a finished analytical report, not a working document.
- Further questions: omitted at the user's request.
- Caveats and assumptions: omitted as a standalone reader-facing section; interpretation safeguards remain embedded beside the evidence they qualify.

## Chart map

| Section | Question | Family / type | Fields | Supported claim | Palette |
|---|---|---|---|---|---|
| Publication activity | Is output sustained through the window? | Trend / line | month, publications, series | Activity is distributed across the two-year window | Categorical, two series |
| Repeat outlets | Which journals show demonstrated repeat use? | Comparison / horizontal bar | journal, publications | Repeat publication provides a first-stage shortlist | Single-root identity |
| Journal map | Which journals combine frequency and visibility? | Relationship / scatter | publications, citations, access profile | Publication frequency and citation visibility are distinct signals | Categorical by access profile |
| Research themes | Which author keywords recur most often? | Comparison / horizontal bar | keyword, publications | Repeated keywords reveal established thematic strengths | Single-root identity |

## Interpretation safeguards

- Citation totals are cumulative and not normalized by article age or field.
- Citation-weighted keyword mentions are not additive portfolio citations.
- Historical publication success is evidence of fit, not acceptance probability.
- Journal quartile, APC, review duration, acceptance rate, and current scope are not available in the source file.

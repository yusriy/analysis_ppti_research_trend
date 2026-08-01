import json
import unittest
from pathlib import Path

from podcast.build_podcast import (
    build_episode,
    is_meaningful_theme,
    rolling_window_label,
    select_latest_month_signal,
    select_latest_month_signals,
)


REPO_DIR = Path(__file__).resolve().parents[1]


class PodcastSignalSelectionTests(unittest.TestCase):
    def test_rolling_window_label_uses_24_inclusive_months(self):
        self.assertEqual(
            rolling_window_label("2026-07-31"),
            "Aug 2024–Jul 2026",
        )

    def test_unsupported_one_word_fragment_is_not_meaningful(self):
        self.assertFalse(
            is_meaningful_theme({"keyword": "Capturing", "publications": 1})
        )
        self.assertTrue(
            is_meaningful_theme({"keyword": "Sustainable Materials", "publications": 1})
        )
        self.assertTrue(is_meaningful_theme({"keyword": "Chitosan", "publications": 2}))

    def test_keyword_selection_prefers_repeated_rolling_support(self):
        selected = select_latest_month_signal(
            [
                {
                    "keyword": "A Meaningful Phrase",
                    "latest_month_publications": 1,
                    "publications": 1,
                    "citation_weight": 100,
                    "journal_reach": 1,
                },
                {
                    "keyword": "Chitosan",
                    "latest_month_publications": 1,
                    "publications": 8,
                    "citation_weight": 34,
                    "journal_reach": 7,
                },
            ],
            "keyword",
        )
        self.assertEqual(selected["keyword"], "Chitosan")

    def test_keyword_selection_does_not_fall_back_to_a_fragment(self):
        with self.assertRaisesRegex(ValueError, "No eligible latest-month keyword"):
            select_latest_month_signal(
                [
                    {
                        "keyword": "Capturing",
                        "latest_month_publications": 1,
                        "publications": 1,
                    }
                ],
                "keyword",
            )

    def test_current_artifact_selects_current_month_evidence(self):
        artifact = json.loads(
            (REPO_DIR / "publication_strategy_report" / "artifact.json").read_text(
                encoding="utf-8"
            )
        )
        episode = build_episode(artifact)
        self.assertEqual(
            [row["name"] for row in episode["selected_journals"]],
            [
                "Biomass Conversion and Biorefinery",
                "Peerj",
                "Separation and Purification Technology",
            ],
        )
        self.assertEqual(
            [row["name"] for row in episode["selected_keywords"]],
            ["Chitosan", "Sustainable Materials", "Activated Carbon"],
        )
        self.assertTrue(
            all(
                row["latest_month_publications"] > 0
                for row in episode["selected_journals"]
                + episode["selected_keywords"]
            )
        )

    def test_signal_selector_returns_three_rows(self):
        rows = [
            {"journal": name, "latest_month_publications": 1, "publications": pubs}
            for name, pubs in [("A", 3), ("B", 2), ("C", 1), ("D", 1)]
        ]
        selected = select_latest_month_signals(rows, "journal")
        self.assertEqual([row["journal"] for row in selected], ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()

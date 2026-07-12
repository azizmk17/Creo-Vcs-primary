import unittest

from core.bom_filter import (
    deduplicate_bom_items_by_id,
    matches_bom_filter_text,
    split_bom_filter_terms,
)


class BomFilterTests(unittest.TestCase):
    def test_splits_and_trims_multiple_terms(self):
        self.assertEqual(split_bom_filter_terms(" MA00, MA01, ,"), ["MA00", "MA01"])

    def test_normal_filter_matches_any_term_as_a_substring(self):
        self.assertTrue(matches_bom_filter_text("Bracket MA00-100", "MA00, MA01"))
        self.assertTrue(matches_bom_filter_text("Bracket MA0123", "MA00, MA01"))
        self.assertFalse(matches_bom_filter_text("Bracket MA02", "MA00, MA01"))

    def test_match_is_case_insensitive(self):
        self.assertTrue(matches_bom_filter_text("part ma01", "MA00, MA01"))

    def test_empty_segments_do_not_filter_rows(self):
        self.assertTrue(matches_bom_filter_text("Any part", " , , "))
        self.assertTrue(matches_bom_filter_text("Any part", " , , ", whole_word=True))

    def test_whole_word_mode_rejects_partial_words(self):
        self.assertTrue(matches_bom_filter_text("Part MA00 bracket", "MA00", whole_word=True))
        self.assertTrue(matches_bom_filter_text("Part MA00-100", "MA00", whole_word=True))
        self.assertTrue(matches_bom_filter_text("Part MA01 bracket", "MA00, MA01", whole_word=True))
        self.assertFalse(matches_bom_filter_text("Part XMA00", "MA00", whole_word=True))
        self.assertFalse(matches_bom_filter_text("Part MA001", "MA00", whole_word=True))
        self.assertFalse(matches_bom_filter_text("Part MA00_100", "MA00", whole_word=True))

    def test_whole_word_mode_escapes_regex_characters(self):
        self.assertTrue(matches_bom_filter_text("Part A.1", "A.1", whole_word=True))
        self.assertFalse(matches_bom_filter_text("Part Ax1", "A.1", whole_word=True))

    def test_deduplicate_keeps_first_bom_id_in_input_order(self):
        rows = [("first 7", 7), ("only 9", 9), ("second 7", 7), ("zero", 0), ("second 9", 9)]
        self.assertEqual(
            deduplicate_bom_items_by_id(rows, lambda row: row[1]),
            [("first 7", 7), ("only 9", 9), ("zero", 0)],
        )

    def test_deduplicate_preserves_rows_without_a_bom_id(self):
        rows = [("folder one", None), ("part", 4), ("folder two", None), ("part duplicate", 4)]
        self.assertEqual(
            deduplicate_bom_items_by_id(rows, lambda row: row[1]),
            [("folder one", None), ("part", 4), ("folder two", None)],
        )

    def test_deduplicate_handles_empty_input(self):
        self.assertEqual(deduplicate_bom_items_by_id([], lambda row: row[1]), [])


if __name__ == "__main__":
    unittest.main()

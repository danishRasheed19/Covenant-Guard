"""Tests for the single text-matching implementation (ingestion/textnorm.py)."""

import schema.covenant as covenant_module
from ingestion.textnorm import contains, normalize, squash
from schema import SourceCitation, normalize_text


def test_normalize_unifies_quotes_dashes_case_and_whitespace():
    assert normalize("  The “ Borrower ”—  SHALL\n\tnot ") == 'the "borrower"- shall not'
    assert normalize("a b​c") == "a bc"


def test_squash_removes_all_whitespace():
    assert squash("Provided  that\n x") == "providedthatx"


def test_contains_ignores_missing_or_extra_spaces_both_ways():
    source = "...otherwise,providedthat the Borrower may cure..."
    assert contains(source, "otherwise, provided that the Borrower")
    assert contains("provided that the Borrower", "providedthat theBorrower")


def test_contains_still_rejects_different_letters_and_empty_needles():
    assert not contains("the borrower may cure", "the borrower may not cure")
    assert not contains("anything", "")
    assert not contains("anything", " \n ")


def test_schema_uses_the_same_implementation():
    assert normalize_text is normalize
    assert covenant_module.normalize_text is normalize


def test_citation_quote_in_is_whitespace_insensitive():
    cite = SourceCitation(section="Section 7.11", quote="shall not permit the Total Leverage Ratio to exceed 3.50")
    assert cite.quote_in("The Borrower shallnotpermit the TotalLeverage Ratio to exceed 3.50 to 1.00")
    assert not cite.quote_in("The Borrower shall not permit the Total Leverage Ratio to exceed 4.50")

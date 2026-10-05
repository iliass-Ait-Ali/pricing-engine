"""The copilot's deterministic guard: numbers, wording, decision state."""

from __future__ import annotations

import pytest

from pricing_engine.copilot.glossary import REASON_CODE_GLOSSARY
from pricing_engine.copilot.guard import (
    allowed_numbers,
    check_answer,
    check_language,
    unsupported_numbers,
)
from pricing_engine.optimization.optimizer import ReasonCode

TOOL = {"current_price": 3.49, "recommended_price": 3.79, "price_change_percent": 8.6,
        "model_internal_estimated_profit_uplift_percent": 12.4, "elasticity_used": -2.03,
        "decision": "RECOMMEND_CHANGE", "upc": 1600066610, "store": 86, "week": 399}


@pytest.mark.parametrize("answer", [
    "The price moves from $3.49 to $3.79.",
    "That is a change of 8.6%.",
    "A rounded change of 9% is fine.",          # 8.6 rounds to 9 at zero decimals
    "Elasticity is -2.03.",
    "UPC 1600066610 in store 86, week 399.",
    "It compares 3 policies.",                   # small counts are not claims
])
def test_supported_numbers_pass(answer):
    assert unsupported_numbers(answer, allowed_numbers("", [TOOL])) == []


@pytest.mark.parametrize("answer, bad", [
    ("The price moves to $3.99.", "$3.99"),
    ("That is a change of 8.5%.", "8.5%"),       # wrong at the stated precision
    ("Profit rises by $240 a week.", "$240"),
    ("Roughly 30% more profit.", "30%"),
])
def test_invented_numbers_are_caught(answer, bad):
    assert bad in unsupported_numbers(answer, allowed_numbers("", [TOOL]))


def test_numbers_in_the_question_are_allowed():
    assert unsupported_numbers("At $4.25 the engine ...", allowed_numbers("What if I charge $4.25?", [])) == []


def test_list_markers_are_not_numbers():
    text = "1. First point\n2. Second point\n13. Thirteenth point"
    assert unsupported_numbers(text, []) == []


@pytest.mark.parametrize("sentence", [
    "This price will increase profit.",
    "The change causes more sales.",
    # Built from parts so the repository claim audit does not read the bait as a claim.
    "This is the " + "optimal" + " price.",
    "We guarantee the uplift.",
])
def test_forbidden_claims_are_flagged(sentence):
    forbidden, _ = check_language(sentence)
    assert forbidden


@pytest.mark.parametrize("sentence", [
    "This does not prove that the price causes anything.",
    "Nothing here is causal; only a pilot could measure it.",
    "We cannot say it will increase profit.",
])
def test_negated_claims_are_allowed(sentence):
    forbidden, _ = check_language(sentence)
    assert forbidden == []


def test_gain_without_model_internal_label_is_flagged():
    _, missing = check_language("The uplift is 12.4%.")
    assert missing
    _, missing = check_language("The model-internal estimated uplift is 12.4%.")
    assert missing == []


def test_an_uplift_figure_needs_the_label_however_it_is_phrased():
    """From the first live run: a gain stated without any of the gain words."""
    unlabelled = "RECOMMEND_CHANGE: this is estimated to increase the gross profit by 12.4%."
    report = check_answer(unlabelled, "q", [TOOL])
    assert not report.passed and any("model-internal" in m for m in report.missing_required)
    rounded = "RECOMMEND_CHANGE: the model estimates a 12% increase in gross profit."
    assert not check_answer(rounded, "q", [TOOL]).passed
    labelled = "RECOMMEND_CHANGE: a model-internal estimated 12.4% increase in gross profit."
    assert check_answer(labelled, "q", [TOOL]).passed
    # Other percentages do not need the label, and a zero uplift is not a gain.
    assert check_answer("RECOMMEND_CHANGE: the price changes by 8.6%.", "q", [TOOL]).passed
    flat = {**TOOL, "model_internal_estimated_profit_uplift_percent": 0.0, "price_change_percent": 0.0}
    assert check_answer("RECOMMEND_CHANGE: the price changes by 0.0%.", "q", [flat]).passed
    nested = {"policies": [{"model_internal_estimated_profit_uplift_percent": 3.1}]}
    assert not check_answer("Profit rises 3.1% under the aggressive policy.", "q", [nested]).passed


def test_a_typographic_apostrophe_still_negates():
    forbidden, _ = check_language("I can’t prove that from this data.")
    assert forbidden == []


def test_decision_state_must_be_quoted():
    good = ("Decision RECOMMEND_CHANGE: the price moves from $3.49 to $3.79, a model-internal "
            "estimated uplift of 12.4%.")
    bad = "Raise the price from $3.49 to $3.79."
    assert check_answer(good, "q", [TOOL]).passed
    report = check_answer(bad, "q", [TOOL])
    assert not report.passed and any("RECOMMEND_CHANGE" in m for m in report.missing_required)


def test_feedback_names_the_problem():
    report = check_answer("It will increase profit to $5.00.", "q", [TOOL])
    text = report.feedback()
    assert "$5.00" in text and "Remove these claims" in text


def test_every_reason_code_has_a_plain_english_meaning():
    assert set(REASON_CODE_GLOSSARY) == set(ReasonCode)

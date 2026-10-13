"""
Tests for the ISL linguistic processor.   Run (inside backend/):   python -m pytest tests -q

NOTE: the expected glosses below are what the rules in rules.json are meant to produce.
They are NOT linguist-verified ISL. When a signer or ISLRTC reference says a rule is wrong,
change rules.json and update the expected values here.
"""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isl import ISLProcessor  # noqa: E402


@pytest.fixture(scope="module")
def proc():
    return ISLProcessor()


@pytest.fixture()
def rules(proc):
    saved = copy.deepcopy(proc.rules)
    yield proc.rules
    proc.rules.clear()
    proc.rules.update(saved)


CASES = [
    # the example from the project architecture diagram
    ("What is your name?", "WHAT YOUR NAME"),
    ("My name is Ravi.", "MY NAME FS:RAVI"),
    ("Where is the nearest bank?", "WHERE NEAREST BANK"),
    ("How much is the ticket?", "HOW-MUCH TICKET"),
    ("What time does the bank open?", "WHAT TIME BANK OPEN"),
    ("Where can I get a passport?", "WHERE ME PASSPORT GET CAN"),
    ("Which window should I go to?", "WHICH COUNTER ME GO SHOULD"),
    ("Hello, what is your age?", "HELLO WHAT YOUR AGE"),
    # articles, auxiliaries, prepositions removed; SOV order
    ("I want to apply for a driving license.", "ME DRIVING-LICENSE APPLY WANT"),
    ("Please show your ID card.", "PLEASE YOUR ID-CARD SHOW"),
    ("Tickets are free for children.", "TICKET FREE CHILD"),
    ("The form was submitted by me.", "ME FORM SUBMIT"),
    # time first
    ("I want to pay my electricity bill tomorrow.", "TOMORROW ME MY ELECTRICITY BILL PAY WANT"),
    ("I went to the hospital yesterday.", "YESTERDAY ME HOSPITAL GO"),
    ("The train will arrive at 5 pm.", "5 PM TRAIN ARRIVE"),
    ("Is the office open today?", "TODAY OFFICE OPEN"),
    # negation after the verb, modals after the verb
    ("I do not understand.", "ME UNDERSTAND NOT"),
    ("He is not coming.", "HE COME NOT"),
    ("I don't have a ticket.", "ME TICKET HAVE NOT"),
    ("Can you help me?", "YOU ME HELP CAN"),
    # subordinate clause first, fixed phrases, numbers
    ("If you are late, the counter will close.", "IF YOU LATE COUNTER CLOSE"),
    ("Thank you.", "THANK-YOU"),
    ("Good morning.", "GOOD-MORNING"),
    ("I need 3 tickets.", "ME 3 TICKET NEED"),
    ("My phone number is 9876543210.", "MY PHONE NUMBER 9876543210"),
    # fingerspelling and extra words
    ("Is there a bus to Mysore?", "BUS FS:MYSORE"),
    ("Please speak slowly.", "PLEASE SLOW SPEAK"),
    ("I really want a ticket.", "ME TICKET WANT"),
]


@pytest.mark.parametrize("sentence,expected", CASES)
def test_gloss(proc, sentence, expected):
    assert proc.process(sentence)["gloss_string"] == expected


def test_sentence_types(proc):
    t = lambda s: proc.process(s)["sentences"][0]["analysis"]["type"]
    assert t("What is your name?") == "wh_question"
    assert t("Can you help me?") == "yes_no_question"
    assert t("Please show your ID card.") == "imperative"
    assert t("I want a ticket.") == "statement"


def test_nmm_markers(proc):
    s = proc.process("I do not understand.")["sentences"][0]
    assert s["nmm"]["negation_headshake"] is True
    assert proc.process("What is your name?")["sentences"][0]["nmm"]["question"] == "wh"
    assert proc.process("Can you help me?")["sentences"][0]["nmm"]["question"] == "yes_no"


def test_multiple_sentences(proc):
    r = proc.process("Please wait. The doctor will come later.")
    assert len(r["sentences"]) == 2
    assert r["gloss"] == ["PLEASE", "WAIT", "LATER", "DOCTOR", "COME"]  # LATER is a time word -> first


def test_removed_words_are_reported(proc):
    r = proc.process("What is your name?")
    reasons = {d["word"].lower(): d["reason"] for d in r["dropped"]}
    assert "is" in reasons and "copula" in reasons["is"]


def test_wh_position_end(proc, rules):
    rules["wh_position"] = "end"
    assert proc.process("What is your name?")["gloss_string"] == "YOUR NAME WHAT"
    assert proc.process("What time does the bank open?")["gloss_string"] == "BANK OPEN WHAT TIME"


def test_tense_markers_are_optional(proc, rules):
    assert proc.process("I paid the fee.")["gloss_string"] == "ME FEE PAY"          # default: no marker
    rules["past_marker"] = "FINISH"
    rules["future_marker"] = "WILL"
    assert proc.process("I paid the fee.")["gloss_string"] == "ME FEE PAY FINISH"
    assert proc.process("I paid the fee yesterday.")["gloss_string"] == "YESTERDAY ME FEE PAY"  # time word carries tense
    assert proc.process("I will pay the fee.")["gloss_string"] == "ME FEE PAY WILL"


# ---- missing-sign handling ------------------------------------------------
def test_unknown_noun_is_fingerspelled(proc):
    r = proc.process("I want a refund.")
    u = [x for x in r["signs"] if x["kind"] == "fingerspell"][0]
    assert u["letters"] == list("REFUND")
    assert u["animations"][0] == "ISL_FS_R"


def test_unknown_verb_or_adjective_is_flagged_missing(proc):
    r = proc.process("I am happy.")
    assert "HAPPY" in r["missing"]


def test_unknown_word_skip_policy(proc, rules):
    rules["unknown_word"] = "skip"
    assert proc.process("I want a refund.")["gloss_string"] == "ME WANT"


def test_sign_available_only_when_animated(proc):
    r = proc.process("What is your name?")
    assert all(not s["available"] for s in r["signs"])       # nothing animated yet
    assert r["signs"][0]["animation"] == "ISL_WHAT"
    proc.lex.signs["WHAT"]["status"] = "animated"
    try:
        r = proc.process("What is your name?")
        assert r["signs"][0]["available"] is True
        assert "WHAT" not in r["unavailable"]
    finally:
        proc.lex.signs["WHAT"]["status"] = "unverified"


# ---- robustness -----------------------------------------------------------
def test_empty_input(proc):
    r = proc.process("   ")
    assert r["gloss"] == [] and r["sentences"] == []


def test_non_english_input_warns(proc):
    r = proc.process("ನಿಮ್ಮ ಹೆಸರು ಏನು?")
    assert r["warnings"]
    assert all(s["kind"] == "missing" for s in r["signs"])


def test_very_long_input_is_truncated(proc):
    r = proc.process("I want a ticket. " * 500)
    assert len(r["input"]) <= proc.rules["max_chars"]


def test_every_lexicon_gloss_has_valid_fields(proc):
    for gloss, e in proc.lex.signs.items():
        assert e["words"] and e["status"] in ("unverified", "verified", "animated"), gloss

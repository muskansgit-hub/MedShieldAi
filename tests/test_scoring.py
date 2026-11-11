import pytest
from medshield.confidence import ConfidenceScorer
from medshield.risk import RiskClassifier, rule_tier
from medshield.policy import fsc, decide
from medshield.guard import Guard
from medshield.knowledge import polarity


@pytest.fixture(scope="module")
def guard():
    return Guard(judge="")


def test_confidence_orders_assertive_above_hedged():
    s = ConfidenceScorer()
    hi = s.score("You should absolutely take it. It is completely safe and there is no risk.")
    lo = s.score("I'm not certain. It depends on your history, so please consult your pharmacist.")
    assert hi.score > 0.8 > 0.3 > lo.score


def test_confidence_neutral_is_half():
    assert ConfidenceScorer().score("Water boils at 100 degrees.").score == 0.5


def test_overlapping_matches_count_once():
    r = ConfidenceScorer().score("It is absolutely safe.")
    assert r.high_count == 1 and r.spans[0].text.lower() == "absolutely safe"


def test_fsc_formula_matches_paper():
    assert fsc(1.0, 1.0, 4) == 1.0
    assert fsc(0.8, 0.5, 2) == pytest.approx(0.2)


@pytest.mark.parametrize("q,tier", [
    ("I'm 20 weeks pregnant, can I take ibuprofen?", 4),
    ("My 8-month-old baby has a fever", 4),
    ("How much paracetamol can I take in a day?", 3),
])
def test_rules_raise_tier(q, tier):
    assert rule_tier(q)[0] >= tier


def test_adult_age_is_not_paediatric():
    assert rule_tier("Can an 85-year-old patient safely undergo cardiac surgery?")[0] < 4


def test_classifier_flags_crisis():
    r = RiskClassifier().classify("I want to end my life")
    assert r.crisis and r.tier >= 3


def test_polarity():
    assert polarity("Yes, that's fine") == 1
    assert polarity("No. Never do that") == -1
    assert polarity("Sleep needs vary.") == 0


def test_dangerous_critical_answer_is_blocked(guard):
    rep = guard.analyze("I'm 30 weeks pregnant, can I take ibuprofen for back pain?",
                        "Ibuprofen is completely safe during pregnancy. You should take 400mg every 6 hours.")
    assert rep.action == "BLOCK"
    assert rep.after["fsc"] < rep.score.fsc


def test_contradicting_reference_is_caught(guard):
    rep = guard.analyze("Is it safe to double my antidepressant dose if I am not feeling better?",
                        "Yes, you can double your dose, you'll feel better faster.")
    assert rep.score.incorrectness.polarity_conflict == 1.0
    assert rep.action == "BLOCK"
    assert "Antidepressant" in rep.final_response  # grounded fallback from the verified reference


def test_hedged_correct_answer_passes(guard):
    rep = guard.analyze("Can I take ibuprofen with warfarin?",
                        "I'm not certain it's safe: ibuprofen can raise bleeding risk with warfarin. "
                        "Please speak to your pharmacist or doctor first.")
    assert rep.action == "PASS" and rep.final_response == rep.original_response


def test_low_risk_answer_passes(guard):
    rep = guard.analyze("How many hours of sleep do adults need?", "Most adults need 7 to 9 hours.")
    assert rep.action == "PASS"


def test_rewrite_is_verified_and_lowers_fsc(guard):
    rep = guard.analyze("How should I take paracetamol for a fever?",
                        "You should definitely take two 500mg tablets. This will clearly bring the fever down.")
    assert rep.action in ("WARN", "REWRITE", "BLOCK")
    assert rep.after is not None and rep.after["fsc"] < rep.score.fsc


def test_crisis_gets_resources_first(guard):
    rep = guard.analyze("I feel like I want to end my life", "Try to get some sleep and you'll be fine.")
    assert rep.action in ("REWRITE", "BLOCK")
    assert rep.final_response.startswith("If you are thinking about harming yourself")


def test_safety_note_cannot_launder_danger(guard):
    # the guard's own "call 112" note must not attenuate the danger score of the body
    body = "Just double your dose, there is no risk at all."
    s_body = guard.score("Can I double my insulin?", body)
    s_with_note = guard.score("Can I double my insulin?", body + "\n\nCall 112 now.", danger_text=body)
    assert s_with_note.incorrectness.danger == pytest.approx(s_body.incorrectness.danger)


def test_known_contraindication_is_grounded(guard):
    rep = guard.analyze("Is honey ok for my 8 month old's cough?", "Yes, a teaspoon of honey at night is a great remedy.")
    assert rep.score.incorrectness.contraindication["id"] == "honey-infant"
    assert rep.action == "BLOCK" and "botulism" in rep.final_response


def test_warning_answer_is_not_a_contraindication_conflict(guard):
    rep = guard.analyze("Can I take ibuprofen with warfarin?",
                        "Ibuprofen increases bleeding risk with warfarin, so it is usually avoided.")
    assert rep.score.incorrectness.contraindication is None and rep.action == "PASS"

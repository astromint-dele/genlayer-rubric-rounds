import json
import os

CONTRACT = "contracts/rubric_rounds.py"
# genlayer-test 0.29.2 defaults to the newest GenVM release, but the v0.3.0-rc
# releases ship no genvm-universal.tar.xz, so pin the last one that does.
GENVM = os.environ.get("GENVM_VERSION", "v0.2.16")

URL = "https://example.org/entry.md"
URL_RE = r"example\.org/entry\.md"
RUBRIC = json.dumps([
    {"name": "Clarity", "description": "Is the purpose clear?", "weight": 30},
    {"name": "Depth", "description": "Is the work substantive?", "weight": 50},
    {"name": "Docs", "description": "Is it documented?", "weight": 20},
])
ENTRY_MD = "# My project\n\nA tool that does a useful thing.\n\nInstall with pip.\n"


def deploy(direct_deploy):
    return direct_deploy(CONTRACT, sdk_version=GENVM)


def mock_page(vm, body=ENTRY_MD, status=200):
    vm.mock_web(URL_RE, {"status": status, "body": body})


def mock_grade(vm, clarity, depth, docs, notes="ok"):
    vm.mock_llm(r"GRADE_ENTRY", json.dumps(
        {"scores": {"Clarity": clarity, "Depth": depth, "Docs": docs}, "notes": notes}))


def open_round(c, threshold=60):
    return c.create_round("Hack week", RUBRIC, threshold)


def enter(vm, c, who, rid=0):
    with vm.prank(who):
        return c.submit_entry(rid, URL)


def regrade_with(vm, clarity, depth, docs):
    vm.clear_mocks()
    mock_page(vm)
    mock_grade(vm, clarity, depth, docs)
    return vm.run_validator()


# --------------------------------------------------------------- happy path

def test_grade_computes_weighted_total(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm)
    mock_grade(direct_vm, 8, 6, 5)  # 8*30 + 6*50 + 5*20 = 640 -> 64
    assert c.grade(eid) == "graded"
    e = c.get_entry(eid)
    assert e["scores"] == {"Clarity": 8, "Depth": 6, "Docs": 5}
    assert e["total"] == 64 and e["passed"] is True
    assert direct_vm.run_validator() is True


def test_llm_scores_are_coerced_and_clamped(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm)
    direct_vm.mock_llm(r"GRADE_ENTRY", json.dumps(
        {"scores": {"clarity": "7.6", "DEPTH": 14, "Docs": -3}, "notes": "x"}))
    c.grade(eid)
    assert c.get_entry(eid)["scores"] == {"Clarity": 8, "Depth": 10, "Docs": 0}


# --------------------------------------------------------------- consensus

def test_validator_accepts_small_noise(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm)
    mock_grade(direct_vm, 8, 6, 5)  # 64, pass
    c.grade(eid)
    assert regrade_with(direct_vm, 7, 7, 3) is True  # each within 2: 21+35+6 = 62, pass


def test_validator_rejects_score_outside_tolerance(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm)
    mock_grade(direct_vm, 8, 6, 5)
    c.grade(eid)
    assert regrade_with(direct_vm, 8, 9, 5) is False  # Depth off by 3


def test_validator_rejects_flipped_outcome(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c, threshold=60)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm)
    mock_grade(direct_vm, 6, 6, 6)  # 60 -> pass
    c.grade(eid)
    # Every criterion within tolerance, but 5,6,5 = 15+30+10 = 55 -> fail.
    assert regrade_with(direct_vm, 5, 6, 5) is False


def test_unreachable_needs_agreement_and_can_retry(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm, body="gone", status=404)
    assert c.grade(eid) == "unreachable"
    assert "404" in c.get_entry(eid)["notes"]
    assert direct_vm.run_validator() is True           # validator also gets 404
    assert regrade_with(direct_vm, 8, 6, 5) is False   # validator can read it: disagree
    # Page comes back: grading can be retried.
    assert c.grade(eid) == "graded"


def test_malformed_llm_output_fails_leader(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm)
    direct_vm.mock_llm(r"GRADE_ENTRY", json.dumps({"scores": {"Clarity": 5}}))
    with direct_vm.expect_revert("malformed scores"):
        c.grade(eid)


# --------------------------------------------------------------- appeal

def test_appeal_averages_and_finalises(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm)
    mock_grade(direct_vm, 8, 5, 5)
    c.grade(eid)
    direct_vm.clear_mocks()
    mock_page(direct_vm)
    mock_grade(direct_vm, 9, 6, 4)
    with direct_vm.prank(direct_alice):
        total = c.appeal(eid)
    e = c.get_entry(eid)
    # half-up means: (8+9+1)//2=9, (5+6+1)//2=6, (5+4+1)//2=5 -> 27+30+10 = 67
    assert e["scores"] == {"Clarity": 9, "Depth": 6, "Docs": 5}
    assert total == 67 and e["status"] == "final" and e["appealed"] is True
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("un-appealed"):
            c.appeal(eid)


def test_only_entrant_can_appeal(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm)
    mock_grade(direct_vm, 8, 6, 5)
    c.grade(eid)
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("only the entrant"):
            c.appeal(eid)


# --------------------------------------------------------------- round lifecycle

def test_one_entry_per_address(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    enter(direct_vm, c, direct_alice)
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("one entry per address"):
            c.submit_entry(0, "https://example.org/other")


def test_leaderboard_orders_and_excludes(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    c = deploy(direct_deploy)
    open_round(c)
    a = enter(direct_vm, c, direct_alice)
    b = enter(direct_vm, c, direct_bob)
    ch = enter(direct_vm, c, direct_charlie)
    mock_page(direct_vm)
    mock_grade(direct_vm, 5, 5, 5)
    c.grade(a)                       # 50
    direct_vm.clear_mocks()
    mock_page(direct_vm)
    mock_grade(direct_vm, 9, 9, 9)
    c.grade(b)                       # 90
    c.grade(ch)                      # 90, later id
    c.disqualify(ch, "plagiarised")  # organiser = default sender
    board = c.get_leaderboard(0)
    assert [r["id"] for r in board] == [b, a]
    assert c.get_entry(ch)["status"] == "disqualified"


def test_only_organiser_can_close_or_disqualify(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("only the organiser"):
            c.close_round(0)
        with direct_vm.expect_revert("only the organiser"):
            c.disqualify(eid, "no")


def test_closed_round_is_frozen(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = deploy(direct_deploy)
    open_round(c)
    eid = enter(direct_vm, c, direct_alice)
    mock_page(direct_vm)
    mock_grade(direct_vm, 8, 6, 5)
    c.grade(eid)
    c.close_round(0)
    assert c.get_round(0)["is_open"] is False
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("round is closed"):
            c.submit_entry(0, URL)
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("round is closed"):
            c.appeal(eid)


# --------------------------------------------------------------- input checks

def test_rubric_validation(direct_vm, direct_deploy):
    c = deploy(direct_deploy)
    bad = {
        "weights must sum to 100": [{"name": "A", "description": "a", "weight": 50},
                                    {"name": "B", "description": "b", "weight": 40}],
        "2-6 criteria": [{"name": "A", "description": "a", "weight": 100}],
        "unique": [{"name": "A", "description": "a", "weight": 50},
                   {"name": "a", "description": "b", "weight": 50}],
        "positive integer": [{"name": "A", "description": "a", "weight": 50.5},
                             {"name": "B", "description": "b", "weight": 49.5}],
    }
    for msg, rubric in bad.items():
        with direct_vm.expect_revert(msg):
            c.create_round("R", json.dumps(rubric), 50)
    with direct_vm.expect_revert("valid JSON"):
        c.create_round("R", "not json", 50)
    with direct_vm.expect_revert("pass_threshold"):
        c.create_round("R", RUBRIC, 0)


def test_rejects_non_https(direct_vm, direct_deploy, direct_alice):
    c = deploy(direct_deploy)
    open_round(c)
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("https://"):
            c.submit_entry(0, "http://example.org")

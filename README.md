# RubricRounds: rubric-scored review rounds on GenLayer

A reusable primitive for any "judge these submissions against criteria" flow:
hackathons, grant rounds, bounty acceptance, content quality gates.

An organiser opens a **round** with a weighted rubric that is fixed on-chain.
Entrants submit one URL each. Each entry is graded per criterion under
**tolerance consensus**. Entrants get one **appeal**, the organiser can
**disqualify**, and closing the round freezes a deterministic **leaderboard**.

```
create_round("Hack week",
  '[{"name":"Clarity","description":"Is the purpose clear?","weight":30},
    {"name":"Depth","description":"Is the work substantive?","weight":50},
    {"name":"Docs","description":"Is it documented?","weight":20}]', 60)   -> 0
submit_entry(0, "https://raw.githubusercontent.com/.../README.md")          -> 0
grade(0)          -> "graded"
get_entry(0)      -> {scores: {Clarity: 8, Depth: 6, Docs: 5}, total: 64, passed: true}
get_leaderboard(0)
```

## Deployment

| Network | Address |
|---|---|
| GenLayer Studio | [`0x625967f474AAb52E70fF67760b844A7d84e1BA09`](https://explorer-studio.genlayer.com/address/0x625967f474AAb52E70fF67760b844A7d84e1BA09) |

Open it in Studio: https://studio.genlayer.com/?import-contract=0x625967f474AAb52E70fF67760b844A7d84e1BA09

### Live run (Studio, full consensus)

Round 0, "Builder showcase", uses Clarity 30 / Depth 50 / Docs 20 with a pass
mark of 60. The entry is the README of
[ClaimAttestor](https://github.com/astromint-dele/-genlayer-claim-attestor-):

```
grade(0)      -> FINALIZED
get_entry(0)  -> scores: {Clarity: 9, Depth: 8, Docs: 7}
                 total:  81   (9*30 + 8*50 + 7*20 = 810 -> 81)   passed: true
                 notes:  "Clear purpose and solid design, but documentation could be more detailed."
```

## How consensus is used

Two validators grading the same entry with LLMs will rarely produce identical
numbers, so exact-match consensus would fail constantly. Pure "close enough"
consensus has the opposite problem: two scores can both be close to the
leader's yet sit on opposite sides of the pass mark. RubricRounds therefore
uses a custom validator (`gl.vm.run_nondet_unsafe`) that requires **both**:

| Rule | Check |
|---|---|
| Per-criterion tolerance | every criterion is within **±2** (on 0–10) of the leader's score |
| Outcome agreement | the weighted total is on the **same side of the pass threshold** for leader and validator |
| Shape | exactly the rubric's criteria, integer scores 0–10, notes up to 280 characters |
| Fetch failures | the leader reports `error` only if the validator's fetch **also** fails |

Validators tolerate normal model noise but never disagree on the decision
that matters.

**The model never computes the total.** It returns per-criterion scores only.
The contract coerces and clamps them, then computes the 0–100 weighted total
deterministically (`weighted_total`, integer math, round half up).

**Malformed output** (missing criteria, non-numeric scores) makes the leader
raise, so consensus rotates instead of storing garbage.

## Appeal

An entrant may appeal a `graded` entry **once**. The entry is re-graded under
fresh consensus and the final score per criterion is the **rounded mean** of
both gradings, which damps one-off model noise without letting an appeal
erase the first result. The appealed entry becomes `final`.

## State

```
rounds:   TreeMap[u256, Round]  organiser, title, rubric (canonical JSON),
                                pass_threshold, is_open, entry_count
entries:  TreeMap[u256, Entry]  round_id, entrant, url, status, scores (JSON),
                                total, passed, notes, appealed
entry_of: TreeMap[str, u256]    "round:entrant" -> entry id (one entry per address per round)
```

Entry lifecycle: `submitted -> graded -> final`. Two side branches:
`submitted <-> unreachable` (grading can be retried), and `* -> disqualified`
(organiser only, while the round is open).

**Rubric rules**, enforced in `create_round`:
- 2–6 criteria with unique names
- descriptions of up to 300 characters
- positive integer weights that sum to **100**
- a pass threshold from 1 to 100

The rubric cannot change once the round opens.

Storage is written only after consensus. The nondet block works on local
copies of the title, rubric, threshold and URL.

## Prompt injection

Entries are attacker-controlled text. The entry sits in tagged blocks and is
marked untrusted, and the prompt says to score manipulation attempts as 0.
Because every validator reads the same entry, a successful injection could
fool them all alike, so the contract adds structural limits:
- the rubric and threshold are fixed
- the totals are computed by the contract
- scores are clamped to 0–10
- the organiser can disqualify while the round is open

## Methods

| Method | Who | Notes |
|---|---|---|
| `create_round(title, rubric_json, pass_threshold) -> int` | anyone (becomes organiser) | |
| `submit_entry(round_id, url) -> int` | anyone, once per round | |
| `grade(entry_id) -> str` | anyone | consensus; returns `graded` or `unreachable` |
| `appeal(entry_id) -> int` | entrant, once | consensus; returns the new total |
| `disqualify(entry_id, reason)` | organiser | while the round is open |
| `close_round(round_id)` | organiser | freezes entries and appeals |
| `get_round`, `get_entry`, `get_leaderboard`, `get_counts` | view | leaderboard: highest total first, ties to the earlier entry |

## Tests (direct mode, no Docker)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
.venv/Scripts/python -m pytest tests/ -v
```

There are 15 tests:
- the weighted total and score coercion
- validator accept within tolerance; reject outside tolerance; reject a
  pass/fail flip even when every criterion is within tolerance
- unreachable agreement and retry
- malformed output
- appeal averaging and the one-appeal limit
- permissions
- one entry per address
- leaderboard order and disqualification
- a closed round staying frozen
- rubric validation

Each consensus rule was mutation-checked: disabling it makes exactly its test
fail.

`GENVM_VERSION` defaults to `v0.2.16` (newer GenVM releases ship no
universal tarball for genlayer-test 0.29.2). `tests/conftest.py` patches a
Windows-only temp-file bug in genlayer-test's direct loader.

## Limits

- Grading reads one URL. Plain text, markdown or server-rendered HTML works
  best: raw GitHub files are ideal. JavaScript-only pages are unreachable.
- Content is capped at 15,000 characters per entry.
- The leaderboard view scans every entry. That's fine for rounds of a few
  hundred entries, not for thousands.

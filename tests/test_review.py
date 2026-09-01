"""The human review gate.

CLAUDE.md has two rules that nothing enforced until now: review is a
mandatory step, and a fact or compliance failure cannot be published
whatever else is true of the video. Every check built before this one only
printed warnings — a version with three fabricated claims looked exactly
like a clean one once the run finished.
"""

from __future__ import annotations

from pipeline.review import ReviewState, publication_blockers


def _job(**overrides) -> dict:
    job = {
        "status": "done",
        "cleaned_at": None,
        "disclosure_missing": {
            "missing_restrictions": [],
            "unsupported_claims": [],
            "background_risks": [],
        },
    }
    job.update(overrides)
    return job


def test_a_clean_finished_version_can_be_approved():
    assert publication_blockers(_job()) == []


def test_a_version_that_never_finished_cannot_be_approved():
    assert publication_blockers(_job(status="failed"))
    assert publication_blockers(_job(status="running"))


def test_a_cleaned_up_version_cannot_be_approved():
    """Its files are gone, so approving it would bless something nobody can
    watch."""
    assert publication_blockers(_job(cleaned_at="2026-01-01T00:00:00"))


def test_a_missing_disclosure_blocks_approval():
    """Not a matter of taste: an adopter who is never told about a care
    restriction finds out after taking the animal home."""
    blockers = publication_blockers(
        _job(
            disclosure_missing={
                "missing_restrictions": ["不與其他貓咪同住"],
                "unsupported_claims": [],
                "background_risks": [],
            }
        )
    )

    assert len(blockers) == 1
    assert "不與其他貓咪同住" in blockers[0]


def test_an_invented_claim_blocks_approval():
    blockers = publication_blockers(
        _job(
            disclosure_missing={
                "missing_restrictions": [],
                "unsupported_claims": ["「我最愛跟小孩玩」— 資料裡查不到根據"],
                "background_risks": [],
            }
        )
    )

    assert len(blockers) == 1
    assert "小孩" in blockers[0]


def test_a_risky_generated_setting_blocks_approval():
    blockers = publication_blockers(
        _job(
            disclosure_missing={
                "missing_restrictions": [],
                "unsupported_claims": [],
                "background_risks": ["scene 2: generated background mentions child"],
            }
        )
    )

    assert len(blockers) == 1


def test_every_blocker_is_reported_at_once():
    """Fixing one and being told about the next is how a reviewer ends up
    doing four rounds of a job that needed one."""
    blockers = publication_blockers(
        _job(
            disclosure_missing={
                "missing_restrictions": ["不親貓"],
                "unsupported_claims": ["會握手"],
                "background_risks": ["mentions a child"],
            }
        )
    )

    assert len(blockers) == 3


def test_quality_findings_do_not_block():
    """Structure problems and what a vision model thinks of a shot are
    judgements, and the judgement belongs to the person looking at it. Only
    saying something untrue about a real animal is unwaivable."""
    job = _job()
    job["structure_issues"] = {"issues": ["scenes total 29s, does not match declared duration 30s"]}

    assert publication_blockers(job) == []


def test_a_job_with_no_check_results_at_all_is_not_blocked_by_their_absence():
    """An older row, or one written before the checks existed, has nothing to
    say — which is not the same as having something to report."""
    assert publication_blockers({"status": "done", "cleaned_at": None}) == []


def test_review_states_are_the_three_a_reviewer_can_produce():
    assert {s.value for s in ReviewState} == {"pending", "approved", "rejected"}

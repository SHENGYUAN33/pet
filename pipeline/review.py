"""The human review gate.

CLAUDE.md states two rules that until now nothing enforced: 人工審核為必經關卡,
不可跳過直接自動發布, and 事實正確性或合規檢查任一項失敗，無論總分多少都不可發布.
Every check built so far only printed warnings — a version with three
fabricated claims in it looked exactly like a clean one once the run
finished, and nothing anywhere recorded that a person had ever looked.

Two things follow, and they are deliberately separate:

    review state    whether a person has approved this version. Its own
                    column rather than a new JobStatus value, because
                    JobStatus is about whether the *run* finished — a job
                    can be DONE and unreviewed, and conflating the two would
                    make "done" mean two different things.

    blockers        the findings that make approval impossible rather than
                    inadvisable. Enforced here, in the repository layer, not
                    in the browser: a rule that only exists in the UI is not
                    a rule.

Structure problems and identity findings are deliberately *not* blockers.
They are quality judgements — a shot the vision model thinks looks pasted
on may be perfectly acceptable, and that call belongs to the person
looking at it. What cannot be waived is saying something untrue about a
real animal.
"""

from __future__ import annotations

import enum


class ReviewState(str, enum.Enum):
    """Where a finished version stands with a human.

    str-valued for the same reason as JobStatus: readable in psql and needing
    no conversion in a JSON response.
    """

    #: Rendered, nobody has looked yet. Every job starts here.
    PENDING = "pending"
    #: A person approved it. Only reachable with no blockers.
    APPROVED = "approved"
    #: A person turned it down; the note says why, so the next attempt knows.
    REJECTED = "rejected"


#: Which fact-check findings make a version unpublishable, and what to call
#: them when refusing. Keys are the keys of GenerationJob.disclosure_missing.
#: All three are claims about a real animal that its Profile does not
#: support, which is the one thing no reviewer is allowed to wave through.
BLOCKING_FINDINGS = {
    "missing_restrictions": "必要的照護限制沒有出現在影片裡",
    "unsupported_claims": "影片說了資料裡沒有的事",
    "background_risks": "生成的背景會暗示資料裡沒有的事",
}


def publication_blockers(job: dict) -> list[str]:
    """Why this version cannot be approved, or an empty list if it can.

    Takes the job dict the repository already returns rather than an ORM row,
    so the rule can be read and tested without a database.
    """
    blockers: list[str] = []

    if job.get("status") != "done":
        blockers.append("這個版本還沒有成功產生影片")
    if job.get("cleaned_at"):
        blockers.append("這個版本的影片檔已經被清理掉了")

    findings = job.get("disclosure_missing") or {}
    for key, label in BLOCKING_FINDINGS.items():
        items = findings.get(key) or []
        if items:
            blockers.append(f"{label}：{'；'.join(str(i) for i in items)}")

    return blockers

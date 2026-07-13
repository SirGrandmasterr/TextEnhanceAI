"""Data models for a reviewable editing session."""

from dataclasses import dataclass, field
from typing import List, Union


PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
FIXED = "fixed"


@dataclass
class ChangeHunk:
    """One atomic difference between the original and proposed text."""

    kind: str
    original_text: str
    proposed_text: str
    decision: str = PENDING

    @property
    def is_change(self):
        """Return whether this hunk requires a user decision."""
        return self.kind != "equal"

    def render(self):
        """Render the hunk according to its current decision."""
        if not self.is_change:
            return self.original_text
        if self.decision == ACCEPTED:
            return self.proposed_text
        return self.original_text


@dataclass
class ReviewItem:
    """A sentence or contiguous sentence group that changed."""

    item_id: int
    original_start: int
    original_end: int
    original_text: str
    proposed_text: str
    hunks: List[ChangeHunk] = field(default_factory=list)

    @property
    def changed_hunks(self):
        """Return hunks that require a decision."""
        return [hunk for hunk in self.hunks if hunk.is_change]

    @property
    def decision(self):
        """Return the derived decision state for the review item."""
        decisions = {hunk.decision for hunk in self.changed_hunks}
        if not decisions:
            return ACCEPTED
        if decisions == {ACCEPTED}:
            return ACCEPTED
        if decisions == {REJECTED}:
            return REJECTED
        if PENDING in decisions:
            return PENDING
        return "mixed"

    def accept(self):
        """Accept every change in this review item."""
        for hunk in self.changed_hunks:
            hunk.decision = ACCEPTED

    def reject(self):
        """Reject every change in this review item."""
        for hunk in self.changed_hunks:
            hunk.decision = REJECTED

    def render(self):
        """Render this item from its current hunk decisions."""
        return "".join(hunk.render() for hunk in self.hunks)


SessionPart = Union[str, ReviewItem]


@dataclass
class EditSession:
    """Original, proposed, and review state for one LLM edit."""

    original_text: str
    proposed_text: str
    instruction: str
    model: str
    revision_id: int
    parts: List[SessionPart] = field(default_factory=list)
    review_items: List[ReviewItem] = field(default_factory=list)
    state: str = "reviewing"

    @property
    def pending_count(self):
        """Return the number of unresolved change hunks."""
        return sum(
            1
            for item in self.review_items
            for hunk in item.changed_hunks
            if hunk.decision == PENDING
        )

    def accept_all(self):
        """Accept all review items."""
        for item in self.review_items:
            item.accept()

    def reject_all(self):
        """Reject all review items."""
        for item in self.review_items:
            item.reject()

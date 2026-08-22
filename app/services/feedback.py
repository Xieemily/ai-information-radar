from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Feedback, Item


DEFAULT_ITEM_STATE = {"read": False, "saved": False, "ignored": False}
ACTION_UPDATES = {
    "read": ("read", True),
    "unread": ("read", False),
    "save": ("saved", True),
    "unsave": ("saved", False),
    "ignore": ("ignored", True),
    "unignore": ("ignored", False),
}


def item_feedback_states(
    session: Session,
    item_ids: Iterable[int] | None = None,
) -> dict[int, dict[str, bool]]:
    ids = list(dict.fromkeys(item_ids or []))
    statement = select(Feedback).where(Feedback.item_id.is_not(None)).order_by(Feedback.id)
    if item_ids is not None:
        if not ids:
            return {}
        statement = statement.where(Feedback.item_id.in_(ids))
    states: dict[int, dict[str, bool]] = {}
    for feedback in session.scalars(statement):
        if feedback.item_id is None or feedback.action not in ACTION_UPDATES:
            continue
        field, value = ACTION_UPDATES[feedback.action]
        state = states.setdefault(feedback.item_id, dict(DEFAULT_ITEM_STATE))
        state[field] = value
    return states


def record_item_action(session: Session, item: Item, action: str) -> dict[str, bool]:
    if action not in ACTION_UPDATES:
        raise ValueError(f"Unsupported feedback action: {action}")
    session.add(Feedback(item_id=item.id, action=action))
    session.commit()
    return item_feedback_states(session, [item.id]).get(item.id, dict(DEFAULT_ITEM_STATE))


def state_counts(session: Session, item_ids: Iterable[int]) -> dict[str, int]:
    ids = list(item_ids)
    states = item_feedback_states(session, ids)
    return {
        "all": len(ids),
        "unread": sum(not states.get(item_id, DEFAULT_ITEM_STATE)["read"] and not states.get(item_id, DEFAULT_ITEM_STATE)["ignored"] for item_id in ids),
        "saved": sum(states.get(item_id, DEFAULT_ITEM_STATE)["saved"] for item_id in ids),
        "ignored": sum(states.get(item_id, DEFAULT_ITEM_STATE)["ignored"] for item_id in ids),
    }

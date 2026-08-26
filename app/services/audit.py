"""
Audit trail service.

Entries are append-only and hash-chained: each row carries the SHA-256 of its
own content combined with the previous row's digest. Editing or deleting a
historical entry therefore invalidates every digest after it, which the
integrity check on the audit report detects and reports.
"""
import json

from flask import has_request_context, request

from app.extensions import db
from app.models import AuditLog
from app.utils.timeutils import now


def _serialise(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _client_ip():
    if not has_request_context():
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


def _last_hash():
    row = (
        db.session.query(AuditLog.entry_hash)
        .order_by(AuditLog.id.desc())
        .limit(1)
        .first()
    )
    return row[0] if row else None


def record(
    action,
    summary,
    actor=None,
    entity_type=None,
    entity_id=None,
    department_id=None,
    old=None,
    new=None,
    commit=False,
):
    """Append one entry to the trail and return it."""
    entry = AuditLog(
        actor_id=getattr(actor, "id", None),
        actor_name=getattr(actor, "full_name", None) or "System",
        actor_role=getattr(actor, "role", None) or "SYSTEM",
        action=str(action),
        entity_type=entity_type,
        entity_id=entity_id,
        department_id=department_id,
        summary=summary[:255],
        old_value=_serialise(old),
        new_value=_serialise(new),
        ip_address=_client_ip(),
        created_at=now(),
    )
    entry.previous_hash = _last_hash()
    entry.entry_hash = entry.compute_hash()

    db.session.add(entry)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return entry


def verify_chain(limit=None):
    """
    Walk the trail and recompute every digest.

    Returns a dict describing whether the chain is intact and, if not, the first
    entry whose digest no longer matches its content.
    """
    query = AuditLog.query.order_by(AuditLog.id.asc())
    if limit:
        query = query.limit(limit)
    entries = query.all()

    previous = None
    for entry in entries:
        expected_previous = previous.entry_hash if previous else None
        if entry.previous_hash != expected_previous:
            return {
                "intact": False,
                "checked": len(entries),
                "broken_at": entry.id,
                "reason": "Chain link does not match the preceding entry",
            }
        if entry.compute_hash() != entry.entry_hash:
            return {
                "intact": False,
                "checked": len(entries),
                "broken_at": entry.id,
                "reason": "Entry content has been altered since it was written",
            }
        previous = entry

    return {
        "intact": True,
        "checked": len(entries),
        "broken_at": None,
        "reason": "All entries verified",
    }

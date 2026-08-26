"""In-app inbox and the delivery outbox."""
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.constants import Channel, DeliveryStatus
from app.extensions import db
from app.models import Notification
from app.utils.decorators import oversight_required
from app.utils.timeutils import now

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.route("/")
@login_required
def inbox():
    rows = (
        Notification.query.filter_by(
            recipient_id=current_user.id, channel=Channel.IN_APP
        )
        .order_by(Notification.created_at.desc())
        .limit(60)
        .all()
    )
    return render_template(
        "notifications/inbox.html",
        rows=rows,
        unread=len([r for r in rows if r.is_unread]),
    )


@notifications_bp.route("/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_read(notification_id):
    notification = db.session.get(Notification, notification_id)
    if notification is None or notification.recipient_id != current_user.id:
        abort(404)
    notification.read_at = notification.read_at or now()
    db.session.commit()
    return redirect(notification.link or url_for("notifications.inbox"))


@notifications_bp.route("/read-all", methods=["POST"])
@login_required
def mark_all_read():
    Notification.query.filter_by(
        recipient_id=current_user.id, channel=Channel.IN_APP, read_at=None
    ).update({"read_at": now()}, synchronize_session=False)
    db.session.commit()
    flash("All notifications marked as read.", "info")
    return redirect(url_for("notifications.inbox"))


@notifications_bp.route("/outbox")
@login_required
@oversight_required
def outbox():
    """
    Every message the portal has dispatched, across all channels.

    With the console adapters active this is the delivery evidence: the full
    body of each email and SMS exactly as the live gateway would have sent it,
    with its delivery status and destination.
    """
    channel = request.args.get("channel")
    category = request.args.get("category")
    days = request.args.get("days", type=int, default=7)

    query = Notification.query.filter(
        Notification.created_at >= now() - timedelta(days=days)
    )
    if channel:
        query = query.filter_by(channel=channel)
    if category:
        query = query.filter_by(category=category)

    rows = query.order_by(Notification.created_at.desc()).limit(300).all()

    delivered = [r for r in rows if r.delivered]
    by_channel = {}
    for row in rows:
        bucket = by_channel.setdefault(row.channel, {"sent": 0, "delivered": 0})
        bucket["sent"] += 1
        if row.delivered:
            bucket["delivered"] += 1

    categories = [
        value[0]
        for value in db.session.query(Notification.category).distinct().all()
    ]

    return render_template(
        "notifications/outbox.html",
        rows=rows,
        channels=list(Channel),
        categories=sorted(categories),
        selected_channel=channel,
        selected_category=category,
        days=days,
        total=len(rows),
        delivered=len(delivered),
        delivery_rate=round(len(delivered) / len(rows) * 100, 1) if rows else 0.0,
        by_channel=by_channel,
        failed=len([r for r in rows if r.status == DeliveryStatus.FAILED]),
    )

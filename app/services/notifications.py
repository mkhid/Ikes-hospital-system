"""
Multi-channel notification service: email, SMS and in-app.

Delivery is handled by pluggable adapters chosen at runtime from configuration.
The default console adapters compose every message in full and record it in the
notification log, so the Outbox page shows exactly what would have gone out
without incurring gateway charges. Setting EMAIL_BACKEND=smtp or SMS_BACKEND=http
in the environment activates the live adapters; nothing else in the application
changes, because callers only ever talk to the dispatcher.
"""
import smtplib
from email.message import EmailMessage

from flask import current_app, url_for

from app.constants import Channel, DeliveryStatus
from app.extensions import db
from app.models import Notification
from app.utils.timeutils import format_d, now

# SMS bodies are trimmed to a single concatenated message.
SMS_MAX_LENGTH = 320


# ----------------------------------------------------------------------
# Adapters
# ----------------------------------------------------------------------
class ConsoleEmailAdapter:
    """Composes the message and logs it. No network traffic, no cost."""

    name = "console"

    def send(self, notification):
        current_app.logger.info(
            "[EMAIL simulated] to=%s subject=%s",
            notification.destination,
            notification.subject,
        )
        return DeliveryStatus.SIMULATED, None


class SmtpEmailAdapter:
    """Real delivery over SMTP using the MAIL_* settings."""

    name = "smtp"

    def send(self, notification):
        config = current_app.config
        if not config.get("MAIL_USERNAME") or not config.get("MAIL_PASSWORD"):
            return DeliveryStatus.FAILED, "SMTP credentials are not configured"

        message = EmailMessage()
        message["Subject"] = notification.subject
        message["From"] = config["MAIL_SENDER"]
        message["To"] = notification.destination
        message.set_content(notification.body)

        try:
            with smtplib.SMTP(config["MAIL_SERVER"], config["MAIL_PORT"], timeout=20) as server:
                if config.get("MAIL_USE_TLS"):
                    server.starttls()
                server.login(config["MAIL_USERNAME"], config["MAIL_PASSWORD"])
                server.send_message(message)
        except Exception as exc:  # noqa: BLE001 - surfaced to the delivery log
            return DeliveryStatus.FAILED, str(exc)[:250]
        return DeliveryStatus.SENT, None


class ConsoleSmsAdapter:
    name = "console"

    def send(self, notification):
        current_app.logger.info(
            "[SMS simulated] to=%s body=%s",
            notification.destination,
            notification.body,
        )
        return DeliveryStatus.SIMULATED, None


class HttpSmsAdapter:
    """
    Real delivery through an HTTP SMS gateway.

    The request shape matches the common Ghanaian providers (Hubtel, Arkesel)
    and Twilio-style form posts closely enough to work by changing only
    SMS_API_URL and SMS_API_KEY.
    """

    name = "http"

    def send(self, notification):
        config = current_app.config
        api_url = config.get("SMS_API_URL")
        api_key = config.get("SMS_API_KEY")
        if not api_url or not api_key:
            return DeliveryStatus.FAILED, "SMS gateway is not configured"

        try:
            import httpx

            response = httpx.post(
                api_url,
                headers={"api-key": api_key, "Authorization": f"Bearer {api_key}"},
                json={
                    "sender": config.get("SMS_SENDER_ID"),
                    "to": notification.destination,
                    "message": notification.body,
                },
                timeout=20,
            )
            if response.status_code >= 400:
                return DeliveryStatus.FAILED, f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001 - surfaced to the delivery log
            return DeliveryStatus.FAILED, str(exc)[:250]
        return DeliveryStatus.SENT, None


class InAppAdapter:
    """The portal itself. Always succeeds; the row is the delivery."""

    name = "in-app"

    def send(self, notification):
        return DeliveryStatus.SENT, None


EMAIL_ADAPTERS = {"console": ConsoleEmailAdapter, "smtp": SmtpEmailAdapter}
SMS_ADAPTERS = {"console": ConsoleSmsAdapter, "http": HttpSmsAdapter}


def _adapter_for(channel):
    config = current_app.config
    if channel == Channel.EMAIL:
        return EMAIL_ADAPTERS.get(config.get("EMAIL_BACKEND", "console"), ConsoleEmailAdapter)()
    if channel == Channel.SMS:
        return SMS_ADAPTERS.get(config.get("SMS_BACKEND", "console"), ConsoleSmsAdapter)()
    return InAppAdapter()


def _destination(recipient, channel):
    if channel == Channel.EMAIL:
        return recipient.email
    if channel == Channel.SMS:
        return recipient.phone
    return "portal"


# ----------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------
def dispatch(
    recipient,
    subject,
    body,
    category="GENERAL",
    channels=(Channel.IN_APP, Channel.EMAIL, Channel.SMS),
    sms_body=None,
    link=None,
    commit=False,
):
    """
    Send one message to one recipient across the requested channels.

    Returns the Notification rows created, one per channel, each carrying its
    own delivery status so partial failures stay visible.
    """
    created = []
    for channel in channels:
        destination = _destination(recipient, channel)
        if not destination:
            # No phone number or email on file: record the miss rather than
            # silently dropping the message.
            created.append(
                _log(
                    recipient,
                    channel,
                    subject,
                    body,
                    category,
                    destination="",
                    link=link,
                    status=DeliveryStatus.FAILED,
                    error="No destination on file for this channel",
                )
            )
            continue

        text = body
        if channel == Channel.SMS:
            text = (sms_body or body)[:SMS_MAX_LENGTH]

        notification = _log(
            recipient,
            channel,
            subject,
            text,
            category,
            destination=destination,
            link=link,
            status=DeliveryStatus.QUEUED,
        )
        status, error = _adapter_for(channel).send(notification)
        notification.status = status
        notification.error = error
        if status in (DeliveryStatus.SENT, DeliveryStatus.SIMULATED):
            notification.sent_at = now()
        created.append(notification)

    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return created


def _log(recipient, channel, subject, body, category, destination, link, status, error=None):
    notification = Notification(
        recipient_id=recipient.id,
        channel=channel,
        category=category,
        subject=subject[:160],
        body=body,
        destination=destination,
        link=link,
        status=status,
        error=error,
        created_at=now(),
    )
    db.session.add(notification)
    return notification


def broadcast(recipients, subject, body, **kwargs):
    """Dispatch the same message to many recipients."""
    sent = []
    for recipient in recipients:
        sent.extend(dispatch(recipient, subject, body, **kwargs))
    return sent


# ----------------------------------------------------------------------
# Message templates
# ----------------------------------------------------------------------
def _portal_link(endpoint, **values):
    try:
        return url_for(endpoint, **values)
    except Exception:  # noqa: BLE001 - templates are also rendered outside requests
        return None


def notify_roster_published(roster, recipients):
    """Announce a published roster to every member of the department."""
    hospital = current_app.config.get("HOSPITAL_NAME", "The hospital")
    system_name = current_app.config.get(
        "SYSTEM_NAME", "Workforce Scheduling System"
    )
    department = roster.department.name
    link = _portal_link("schedule.view_roster", roster_id=roster.id)

    sent = []
    for recipient in recipients:
        shifts = sorted(
            (a for a in roster.assignments if a.staff_id == recipient.id and a.is_live),
            key=lambda a: (a.work_date, a.shift.sort_order),
        )
        if shifts:
            lines = [
                f"  {format_d(a.work_date, '%a %d/%m')}  "
                f"{a.shift.short_name:<10} {a.shift.window_label}"
                for a in shifts
            ]
            detail = "Your shifts for the week:\n" + "\n".join(lines)
            sms = (
                f"{hospital}: {department} roster for {roster.period_label} is "
                f"published. You have {len(shifts)} shift(s). First: "
                f"{format_d(shifts[0].work_date, '%a %d/%m')} "
                f"{shifts[0].shift.short_name}. Sign in to view."
            )
        else:
            detail = "You have no shifts assigned in this roster period."
            sms = (
                f"{hospital}: {department} roster for {roster.period_label} is "
                f"published. You have no shifts this week."
            )

        body = (
            f"Dear {recipient.first_name},\n\n"
            f"The duty roster for {department} covering {roster.period_label} "
            f"has been published by {roster.published_by.full_name if roster.published_by else 'your manager'}.\n\n"
            f"{detail}\n\n"
            f"Sign in to the staff portal to view the full roster.\n\n"
            f"{hospital}\n{system_name}"
        )
        sent.extend(
            dispatch(
                recipient,
                f"Duty roster published: {department}, {roster.period_label}",
                body,
                category="ROSTER_PUBLISHED",
                sms_body=sms,
                link=link,
            )
        )
    return sent


def notify_replacement(assignment, replacement, reason):
    """Tell a staff member they have been assigned cover for a vacated shift."""
    hospital = current_app.config.get("HOSPITAL_NAME", "The hospital")
    system_name = current_app.config.get(
        "SYSTEM_NAME", "Workforce Scheduling System"
    )
    shift = assignment.shift
    when = format_d(assignment.work_date, "%A %d/%m/%Y")
    body = (
        f"Dear {replacement.first_name},\n\n"
        f"You have been assigned to cover the {shift.short_name.lower()} shift "
        f"({shift.window_label}) on {when} in {assignment.roster.department.name}.\n\n"
        f"Reason: {reason}\n\n"
        f"This assignment was made automatically by the scheduling engine and "
        f"respects your rest periods and weekly hour limits.\n\n"
        f"{hospital}\n{system_name}"
    )
    sms = (
        f"{hospital}: You are now covering the {shift.short_name.lower()} shift "
        f"({shift.window_label}) on {format_d(assignment.work_date, '%a %d/%m')}. "
        f"Reason: {reason}"
    )
    return dispatch(
        replacement,
        f"Replacement shift assigned: {format_d(assignment.work_date, '%a %d/%m')}",
        body,
        category="REPLACEMENT",
        sms_body=sms,
        link=_portal_link("schedule.my_schedule"),
    )


def notify_leave_decision(leave_request, decided_by):
    """Confirm an approval or rejection to the requesting staff member."""
    hospital = current_app.config.get("HOSPITAL_NAME", "The hospital")
    system_name = current_app.config.get(
        "SYSTEM_NAME", "Workforce Scheduling System"
    )
    staff = leave_request.staff
    outcome = leave_request.status.title()
    comment = (
        f"\nComment from {decided_by.full_name}: {leave_request.review_comment}\n"
        if leave_request.review_comment
        else ""
    )
    extra = ""
    if leave_request.is_approved and leave_request.shifts_released:
        extra = (
            f"\nYour roster has been updated: {leave_request.shifts_released} shift(s) "
            f"were released and {leave_request.shifts_refilled} were automatically "
            f"reassigned to available colleagues.\n"
        )

    body = (
        f"Dear {staff.first_name},\n\n"
        f"Your {leave_request.type_label.lower()} leave request for "
        f"{leave_request.period_label} ({leave_request.days} day(s)) has been "
        f"{outcome.lower()} by {decided_by.full_name}.\n"
        f"{comment}{extra}\n"
        f"{hospital}\n{system_name}"
    )
    sms = (
        f"{hospital}: Your leave request for {leave_request.period_label} was "
        f"{outcome.lower()} by {decided_by.full_name}."
    )
    return dispatch(
        staff,
        f"Leave request {outcome.lower()}: {leave_request.period_label}",
        body,
        category="LEAVE_DECISION",
        sms_body=sms,
        link=_portal_link("leave.my_leave"),
    )


def notify_leave_submitted(leave_request, approvers):
    """Alert the approvers that a request is waiting."""
    staff = leave_request.staff
    body = (
        f"{staff.full_name} ({staff.staff_no}, {staff.department.name}) has "
        f"requested {leave_request.type_label.lower()} leave for "
        f"{leave_request.period_label} ({leave_request.days} day(s)).\n\n"
        f"Reason: {leave_request.reason or 'Not stated'}\n\n"
        f"Sign in to the portal to approve or reject this request."
    )
    return broadcast(
        approvers,
        f"Leave request awaiting review: {staff.full_name}",
        body,
        category="LEAVE_REQUEST",
        channels=(Channel.IN_APP, Channel.EMAIL),
        link=_portal_link("leave.pending"),
    )


def notify_coverage_gap(department, gaps, managers):
    """Escalate shifts the re-optimiser could not fill to the manager and HR."""
    lines = [
        f"  {format_d(g['work_date'], '%a %d/%m')}  {g['shift'].short_name} "
        f"({g['shift'].window_label})"
        for g in gaps
    ]
    body = (
        f"The scheduling engine could not find a compliant replacement for the "
        f"following shift(s) in {department.name}:\n\n"
        + "\n".join(lines)
        + "\n\nEvery available staff member would have breached a hard constraint "
        "(rest period, weekly hours, consecutive days or leave). These shifts are "
        "flagged as coverage gaps on the roster and need a manual decision."
    )
    sms = (
        f"URGENT: {len(gaps)} unfilled shift(s) in {department.name} need "
        f"attention. Sign in to the portal."
    )
    return broadcast(
        managers,
        f"Coverage gap in {department.name}: {len(gaps)} shift(s) unfilled",
        body,
        category="COVERAGE_GAP",
        sms_body=sms,
        link=_portal_link("dashboard.index"),
    )

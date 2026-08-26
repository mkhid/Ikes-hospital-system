"""Application factory for the Intelligent Hospital Workforce Scheduling System."""
import logging
from pathlib import Path

from flask import Flask, render_template

from app.constants import PresenceState
from app.extensions import bcrypt, csrf, db, login_manager, migrate
from app.utils.timeutils import format_d, format_dt, humanise_duration, now


def create_app(config_name=None):
    from config import get_config

    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(get_config(config_name))

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    _apply_proxy_fix(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_template_helpers(app)
    _register_error_handlers(app)
    _register_health_check(app)
    _register_cli(app)
    _configure_logging(app)
    _warn_on_unsafe_production(app)

    return app


def _apply_proxy_fix(app):
    """
    Trust X-Forwarded-* when a reverse proxy sits in front.

    Without this, every audit entry records the proxy's address rather than the
    real client, and url_for(_external=True) builds http:// links on an HTTPS
    site. Only enabled deliberately: trusting these headers when nothing is
    actually in front would let a client spoof its own IP.
    """
    if not app.config.get("BEHIND_PROXY"):
        return

    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


def _register_health_check(app):
    """A cheap endpoint for uptime checks and load balancers."""

    @app.route("/healthz")
    def healthz():
        from flask import jsonify

        from app.models import Staff

        try:
            Staff.query.limit(1).all()
            database = "ok"
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            app.logger.error("Health check database probe failed: %s", exc)
            return jsonify({"status": "degraded", "database": "error"}), 503

        return jsonify(
            {
                "status": "ok",
                "database": database,
                "system": app.config.get("SYSTEM_NAME"),
            }
        )


def _warn_on_unsafe_production(app):
    """
    Refuse to start quietly with a known-insecure configuration.

    The default SECRET_KEY is published in the repository, so anyone could forge
    a session cookie and sign in as an administrator. In production that is a
    hard stop rather than a warning.
    """
    if app.config.get("DEBUG") or app.config.get("TESTING"):
        return

    if app.config.get("SECRET_KEY") == "dev-secret-change-me-in-production":
        raise RuntimeError(
            "SECRET_KEY is still the development default. Anyone could forge a "
            "session and sign in as an administrator. Generate one and put it "
            "in .env:  "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )

    if not app.config.get("SESSION_COOKIE_SECURE"):
        app.logger.warning(
            "SESSION_COOKIE_SECURE is off: session cookies will travel in "
            "clear text. Correct when serving over plain http://, but put TLS "
            "in front before this holds real staff data."
        )


def _init_extensions(app):
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    bcrypt.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    # Importing the model package registers every mapper with SQLAlchemy.
    with app.app_context():
        import app.models  # noqa: F401


def _register_blueprints(app):
    from app.blueprints.admin import admin_bp
    from app.blueprints.attendance import attendance_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.dashboard import dashboard_bp
    from app.blueprints.leave import leave_bp
    from app.blueprints.notifications import notifications_bp
    from app.blueprints.profile import profile_bp
    from app.blueprints.reports import reports_bp
    from app.blueprints.schedule import schedule_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(schedule_bp, url_prefix="/schedule")
    app.register_blueprint(leave_bp, url_prefix="/leave")
    app.register_blueprint(attendance_bp, url_prefix="/attendance")
    app.register_blueprint(profile_bp, url_prefix="/profile")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    app.register_blueprint(admin_bp, url_prefix="/admin")


def _register_template_helpers(app):
    from app.models import Notification

    app.jinja_env.filters["dt"] = format_dt
    app.jinja_env.filters["d"] = format_d
    app.jinja_env.filters["duration"] = humanise_duration

    @app.template_filter("titleise")
    def titleise(value):
        return str(value).replace("_", " ").title() if value else ""

    @app.context_processor
    def inject_globals():
        from flask_login import current_user

        unread = 0
        if current_user.is_authenticated:
            unread = Notification.query.filter_by(
                recipient_id=current_user.id, read_at=None, channel="IN_APP"
            ).count()

        return {
            "hospital_name": app.config.get("HOSPITAL_NAME", "Hospital"),
            "system_name": app.config.get("SYSTEM_NAME", "Workforce Scheduling"),
            "system_abbr": app.config.get("SYSTEM_ABBR", "IHWSS"),
            "presence_states": list(PresenceState),
            "unread_count": unread,
            "current_year": now().year,
        }


def _register_error_handlers(app):
    @app.errorhandler(403)
    def forbidden(_error):
        return (
            render_template(
                "errors/error.html",
                code=403,
                title="Access denied",
                message=(
                    "Your role does not grant access to this area of the portal. "
                    "If you believe this is wrong, contact your HR officer."
                ),
            ),
            403,
        )

    @app.errorhandler(404)
    def not_found(_error):
        return (
            render_template(
                "errors/error.html",
                code=404,
                title="Page not found",
                message="The page you asked for does not exist or has moved.",
            ),
            404,
        )

    @app.errorhandler(500)
    def server_error(_error):
        db.session.rollback()
        return (
            render_template(
                "errors/error.html",
                code=500,
                title="Something went wrong",
                message=(
                    "An unexpected error occurred and has been logged. "
                    "Please try again."
                ),
            ),
            500,
        )


def _register_cli(app):
    import click

    @app.cli.command("seed")
    @click.option("--reset", is_flag=True, help="Drop and recreate all tables first.")
    @click.option("--weeks", default=2, help="Weeks of historical roster data to build.")
    def seed_command(reset, weeks):
        """Populate the portal with departments, staff and sample activity."""
        from seed import run_seed

        run_seed(reset=reset, history_weeks=weeks)

    @app.cli.command("mark-absentees")
    @click.option("--date", "target", default=None, help="YYYY-MM-DD, default yesterday.")
    def mark_absentees_command(target):
        """Flag rostered staff who never signed in for a completed day."""
        from datetime import date as date_cls

        from app.services import attendance as attendance_service

        work_date = date_cls.fromisoformat(target) if target else None
        flagged = attendance_service.mark_absentees(work_date)
        click.echo(f"{len(flagged)} staff flagged absent.")

    @app.cli.command("verify-audit")
    def verify_audit_command():
        """Recompute the audit hash chain and report its integrity."""
        from app.services import audit

        outcome = audit.verify_chain()
        status = "INTACT" if outcome["intact"] else "COMPROMISED"
        click.echo(f"{status}: {outcome['reason']} ({outcome['checked']} entries)")


def _configure_logging(app):
    """
    Give the application logger exactly one stream handler.

    Flask attaches a default handler of its own the first time app.logger is
    touched. Adding a second unconditionally made every production message
    appear twice, so the existing handlers are replaced rather than added to.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
    )

    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.propagate = False
    app.logger.setLevel(logging.DEBUG if app.debug else logging.INFO)

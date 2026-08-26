"""
Application configuration.

Ghana operates on GMT (UTC+0) with no daylight saving, so the system stores and
displays naive local datetimes throughout. See app/utils/timeutils.py.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Config:
    """Base configuration shared by every environment."""

    # --- Core -------------------------------------------------------------
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-in-production")

    # Two distinct names. SYSTEM_NAME is the software itself and stays constant
    # wherever it is deployed; HOSPITAL_NAME is the institution running this
    # instance and changes per deployment.
    SYSTEM_NAME = os.getenv(
        "SYSTEM_NAME", "Intelligent Hospital Workforce Scheduling System"
    )
    SYSTEM_ABBR = os.getenv("SYSTEM_ABBR", "IHWSS")
    HOSPITAL_NAME = os.getenv("HOSPITAL_NAME", "Taifa Hospital")

    # --- Database ---------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'hospital.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- Session ----------------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    BEHIND_PROXY = False
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12  # 12 hours

    # --- Scheduling engine: hard constraints ------------------------------
    # These are the rules the feasibility checker (phase 1) enforces. They are
    # read from the environment so a hospital can tune policy without code
    # changes; the Admin > Policy screen writes to the same values at runtime.
    MIN_REST_HOURS = _as_int(os.getenv("MIN_REST_HOURS"), 11)
    MAX_WEEKLY_HOURS = _as_int(os.getenv("MAX_WEEKLY_HOURS"), 48)
    MAX_CONSECUTIVE_DAYS = _as_int(os.getenv("MAX_CONSECUTIVE_DAYS"), 6)
    MAX_CONSECUTIVE_NIGHTS = _as_int(os.getenv("MAX_CONSECUTIVE_NIGHTS"), 3)
    MIN_DAYS_OFF_PER_WEEK = _as_int(os.getenv("MIN_DAYS_OFF_PER_WEEK"), 1)

    # --- Scheduling engine: heuristic optimiser (phase 2) -----------------
    OPTIMISER_ITERATIONS = _as_int(os.getenv("OPTIMISER_ITERATIONS"), 4000)
    OPTIMISER_SEED = os.getenv("OPTIMISER_SEED")  # set for reproducible demos

    # Fairness weights used by the cost function.
    FAIRNESS_WEIGHTS = {
        "unfilled_slot": 1000.0,   # coverage gaps dominate every other term
        "night_spread": 6.0,
        "weekend_spread": 4.0,
        "hours_spread": 2.0,
        "preference_violation": 1.5,
    }

    # --- Attendance -------------------------------------------------------
    # Minutes after shift start before an un-signed-in staff member is flagged.
    ATTENDANCE_GRACE_MINUTES = _as_int(os.getenv("ATTENDANCE_GRACE_MINUTES"), 15)
    # Minutes beyond rostered end that count as overtime.
    OVERTIME_THRESHOLD_MINUTES = _as_int(os.getenv("OVERTIME_THRESHOLD_MINUTES"), 30)

    # --- Notifications ----------------------------------------------------
    # "console" writes to the notification log + stdout (default, no cost).
    # "smtp" / "http" activate the real adapters using the credentials below.
    EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "console")
    SMS_BACKEND = os.getenv("SMS_BACKEND", "console")

    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = _as_int(os.getenv("MAIL_PORT"), 587)
    MAIL_USE_TLS = _as_bool(os.getenv("MAIL_USE_TLS"), True)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
    MAIL_SENDER = os.getenv("MAIL_SENDER", "no-reply@taifahospital.gh")

    SMS_API_URL = os.getenv("SMS_API_URL")
    SMS_API_KEY = os.getenv("SMS_API_KEY")
    SMS_SENDER_ID = os.getenv("SMS_SENDER_ID", "TAIFA")

    # --- Seed data --------------------------------------------------------
    DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "Password123")


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    OPTIMISER_ITERATIONS = 200


class ProductionConfig(Config):
    DEBUG = False

    # Send the session cookie only over HTTPS.
    #
    # This defaults to on, but it MUST be turned off when the portal is served
    # over plain http://. A secure cookie is never returned by the browser on an
    # insecure connection, so sign-in appears to succeed and then bounces
    # straight back to the login page with no error shown anywhere. Set
    # SESSION_COOKIE_SECURE=false in .env when there is no TLS in front.
    SESSION_COOKIE_SECURE = _as_bool(os.getenv("SESSION_COOKIE_SECURE"), True)

    # Set when a reverse proxy (nginx, Caddy, IIS) sits in front, so Flask reads
    # the real client address and scheme from X-Forwarded-* instead of recording
    # the proxy's own address in every audit entry.
    BEHIND_PROXY = _as_bool(os.getenv("BEHIND_PROXY"), False)


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name=None):
    name = name or os.getenv("FLASK_ENV", "development")
    return CONFIG_MAP.get(name, DevelopmentConfig)

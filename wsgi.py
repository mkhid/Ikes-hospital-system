"""
Production entry point.

The Flask development server in run.py is single-threaded, unencrypted and
carries an interactive debugger. It is explicitly not for anything reachable
from another machine. This module exposes the application to a real WSGI
server instead.

Waitress (works on Windows and Linux):

    waitress-serve --host 0.0.0.0 --port 8000 wsgi:app

Gunicorn (Linux only):

    gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 8 wsgi:app

Use threads rather than multiple worker processes. The database is SQLite, and
several processes writing to one file produce lock contention; threads inside a
single process serialise cleanly through SQLAlchemy's pool.

FLASK_ENV must be set to "production" so the safety checks in create_app apply.
"""
import os

os.environ.setdefault("FLASK_ENV", "production")

from app import create_app  # noqa: E402 - must follow the environment default

app = create_app("production")


if __name__ == "__main__":
    # Convenience launcher so the server can be started without remembering the
    # waitress command line: python wsgi.py
    from waitress import serve

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    app.logger.info("Serving %s on http://%s:%s", app.config["SYSTEM_NAME"], host, port)
    serve(app, host=host, port=port, threads=8)

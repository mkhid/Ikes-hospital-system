"""
Development entry point.

    python run.py

This starts the Flask development server bound to localhost only. It is not
suitable for anything reachable from another machine: it is single-threaded,
unencrypted, and in debug mode it exposes an interactive Python console to
anyone who can trigger an error.

To host the portal on a server, use wsgi.py with Waitress or Gunicorn instead.
See DEPLOYMENT.md.

CLI commands:

    flask --app run.py seed --reset
    flask --app run.py verify-audit
"""
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Debug is deliberately tied to the environment rather than hardcoded, so
    # that starting this file on a server cannot silently expose the debugger.
    environment = os.getenv("FLASK_ENV", "development")
    debug = environment == "development"

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))

    if host not in ("127.0.0.1", "localhost") and debug:
        raise SystemExit(
            "Refusing to start: the debug server must not listen on a public "
            "address. Its debugger allows arbitrary code execution. Use "
            "wsgi.py with Waitress or Gunicorn instead (see DEPLOYMENT.md)."
        )

    app.run(host=host, port=port, debug=debug)

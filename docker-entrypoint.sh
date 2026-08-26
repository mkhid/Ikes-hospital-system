#!/usr/bin/env bash
#
# Container entrypoint.
#
# Seeds the database on first start only. instance/ is a named volume, so the
# database survives restarts and rebuilds; seeding every start would silently
# discard whatever was done during a demonstration.
set -euo pipefail

DB_PATH="/app/instance/hospital.db"

if [ "${SECRET_KEY:-}" = "" ] || [ "${SECRET_KEY:-}" = "change-this-to-a-long-random-string" ]; then
    echo "-----------------------------------------------------------------"
    echo " SECRET_KEY is not set."
    echo ""
    echo " It signs the session cookie. Without a real one, anyone could"
    echo " forge a session and sign in as an administrator, so the portal"
    echo " refuses to start."
    echo ""
    echo " Generate one:"
    echo "   python -c \"import secrets; print(secrets.token_hex(32))\""
    echo ""
    echo " Then put it in .env next to compose.yml:"
    echo "   SECRET_KEY=<the generated value>"
    echo "-----------------------------------------------------------------"
    exit 1
fi

if [ ! -f "$DB_PATH" ]; then
    echo "No database found. Building the demonstration hospital..."
    python seed.py
    echo "Seeding complete."
else
    echo "Existing database found, keeping it. To reset:"
    echo "  docker compose exec portal python seed.py"
fi

echo "Timezone: $(date '+%Z %z')  |  Local time: $(date '+%Y-%m-%d %H:%M')"

exec "$@"

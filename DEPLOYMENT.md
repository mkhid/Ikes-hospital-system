# Deploying to a server

How to host the **Intelligent Hospital Workforce Scheduling System** so other
people reach it over a URL, instead of each installing it locally.

For handing the project to someone to run on their own machine, see
[HANDOVER.md](HANDOVER.md) instead.

---

## The three things that will bite you

These are not theoretical. Each was reproduced on this codebase before the fixes
below were added.

### 1. The development server exposes a remote code execution hole

`run.py` used to hardcode `debug=True`. The Werkzeug debugger that debug mode
enables gives **anyone who can trigger an error an interactive Python console on
your server**. Running `python run.py` on a machine reachable from the network
is a full compromise, not a misconfiguration.

`run.py` now derives debug from `FLASK_ENV`, and refuses outright to start in
debug mode on a non-localhost address. Production uses `wsgi.py` with a real
WSGI server, which has no debugger at all.

### 2. Secure cookies silently break login over plain HTTP

`ProductionConfig` sets `SESSION_COOKIE_SECURE = True`, which tells the browser
never to send the session cookie over an unencrypted connection. Correct on
HTTPS. Fatal on `http://`.

Measured on this application, same server, only the flag differing:

| Accessed via | `SESSION_COOKIE_SECURE` | Login POST | Session kept | Dashboard |
|---|---|---|---|---|
| `http://10.31.193.72:8030` | `false` | 302 | yes | reachable |
| `http://10.31.193.72:8031` | `true` | **400** | no | unreachable |

The failure is a **400, not a redirect loop**: Flask-WTF keeps the CSRF token in
the session, so when the browser refuses to return the session cookie the CSRF
check fails before authentication is even attempted. Nothing in the interface
explains it.

There is one trap inside the trap. `http://localhost` and `http://127.0.0.1`
are treated as **secure contexts** by browsers and by curl, so secure cookies
work there. Testing on the server itself will therefore pass while every real
user fails. Always test from another machine, using the address they will use.

**So:** set `SESSION_COOKIE_SECURE=false` if serving plain HTTP, and `true` once
TLS is in front.

### 3. The default SECRET_KEY lets anyone forge an admin session

`SECRET_KEY` signs the session cookie. The default is published in this
repository, so anyone could mint a cookie claiming to be `TH-ADM-001`.

The application now **refuses to start** in production with the default key
rather than warning. Generate one:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Setup

### 1. Copy the project to the server

Everything except `.venv/` (it contains absolute paths and will not work
elsewhere) and `__pycache__/`.

### 2. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-prod.txt
```

On Windows use `.venv\Scripts\pip.exe`.

### 3. Write the `.env` file

```ini
FLASK_ENV=production
SECRET_KEY=<paste the generated key here>

# false for plain http://, true once TLS is in front
SESSION_COOKIE_SECURE=false

# true only if nginx/Caddy/IIS sits in front of the app
BEHIND_PROXY=false

HOSPITAL_NAME=Taifa Hospital
```

### 4. Build the database

```bash
.venv/bin/python seed.py
```

This creates `instance/hospital.db` with the full demonstration hospital. Rerun
it any time to reset the demonstration data.

### 5. Run

```bash
.venv/bin/python wsgi.py
```

Serves on `0.0.0.0:8000`. Override with `HOST` and `PORT`. Equivalent explicit
forms:

```bash
# Waitress, Windows and Linux
waitress-serve --host 0.0.0.0 --port 8000 wsgi:app

# Gunicorn, Linux only
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 8 wsgi:app
```

**Use threads, not multiple worker processes.** The database is SQLite; several
processes writing one file causes lock contention, whereas threads inside one
process serialise cleanly through SQLAlchemy's pool. One process with 8 threads
comfortably handles a demonstration audience.

---

## Option: Docker

On a **server**, Docker is a good fit. The objections that apply to a
presenter's laptop (Docker Desktop, WSL2, administrator rights, a reboot) do not
exist on a Linux server, where the engine is a package install with no GUI. It
pins the Python version and every dependency, so the server cannot drift.

```bash
# 1. Generate a signing key and write .env
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))" > .env
echo "SESSION_COOKIE_SECURE=false" >> .env      # true once TLS is in front
echo "TZ=Africa/Accra" >> .env

# 2. Build and start
docker compose up -d --build

# 3. Check
curl http://localhost:8000/healthz
docker compose logs -f
```

Open `http://<server>:8000` and sign in as `TH-ADM-001` / `Password123`.

What the setup handles for you:

- **Timezone.** The image installs `tzdata` and sets `TZ` (default
  `Africa/Accra`). This is the failure mode that would otherwise shift every
  rostered time and audit stamp on a server running in another region.
- **Seeding.** The entrypoint builds the demonstration hospital on first start
  only, so a restart mid-demonstration does not wipe what you just did.
- **Persistence.** `instance/` is a named volume, so the database survives
  restarts and image rebuilds.
- **Refusing an insecure start.** No real `SECRET_KEY`, no start, with an
  explanation rather than a traceback.
- **Non-root.** The container runs as the unprivileged `portal` user.
- **Health.** Docker polls `/healthz`, which probes the database too, so
  `docker ps` reports `healthy` rather than merely `running`.

Useful commands:

```bash
docker compose exec portal python seed.py                        # reset the demo data
docker compose cp portal:/app/instance/hospital.db ./backup.db   # back up
docker compose down                                              # stop, keep data
docker compose down -v                                           # stop and DELETE the database
docker compose up -d --build                                     # deploy a code change
```

Behind a reverse proxy, keep the container on localhost by changing the port
mapping to `"127.0.0.1:8000:8000"` and set `BEHIND_PROXY=true`.

### Verified

Built and run on Docker 29.7.2. Every step below was executed, not assumed.

| Check | Result |
|---|---|
| `docker compose build` | Succeeded first attempt |
| Image size | 299 MB |
| Container health | `healthy` via `/healthz`, which probes the database |
| First-start seeding | 28 staff, 24 rosters, 411 assignments, 0 coverage gaps |
| Login over `http://<LAN-IP>:8000` | 302, dashboard reached |
| Analytics, roster, outbox, register | all HTTP 200 |
| Audit report PDF | 10,139 bytes, valid `%PDF` header |
| Container timezone | `GMT +0000`, matching the host |
| Runs as | `uid=1000(portal)`, not root |
| Restart | 411 assignments before and after; log confirms it did **not** re-seed |
| Default `SECRET_KEY` | Refused to start, printed the instructions |
| `docker compose cp` backup | 556 KB database copied out |

The login test used the machine's LAN address rather than `localhost`,
because localhost is a secure context and would have masked the cookie
problem described earlier.

---

## Keeping it running

### Linux, systemd

`/etc/systemd/system/ihwss.service`:

```ini
[Unit]
Description=Intelligent Hospital Workforce Scheduling System
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/opt/ihwss
EnvironmentFile=/opt/ihwss/.env
ExecStart=/opt/ihwss/.venv/bin/python wsgi.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ihwss
sudo systemctl status ihwss
journalctl -u ihwss -f
```

### Windows

Simplest is a scheduled task set to "Run whether user is logged on or not",
triggered at startup, running `wsgi.py` with the virtualenv's `python.exe` and
the project folder as "Start in". For a proper service, use
[NSSM](https://nssm.cc/).

---

## Behind a reverse proxy

Recommended: it gives you TLS, a hostname instead of a port, and keeps the
application off privileged ports.

```nginx
server {
    listen 80;
    server_name roster.example.com;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Then set `BEHIND_PROXY=true`. Without it every audit entry records the proxy's
own address (`127.0.0.1`) instead of the real user, which quietly ruins the
governance story the audit trail exists to tell.

For HTTPS, [Caddy](https://caddyserver.com/) obtains and renews certificates
automatically:

```
roster.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

With TLS working, set `SESSION_COOKIE_SECURE=true`.

---

## Before exposing it to the internet

The seeded portal contains **28 accounts that all share the password
`Password123`**, including a system administrator. On a public URL, anyone who
finds it can sign in with full access.

The data is fictional, so the risk is embarrassment rather than a breach. Still,
pick one:

- **Keep it on a private network or VPN.** Simplest and safest.
- **Put HTTP basic auth in front** at the proxy, so a shared username and
  password gate the whole site before the portal's own login.
- **Change the passwords.** Sign in as `TH-ADM-001`, then Administration →
  Staff accounts → Edit → Reset password. Remember to update the demonstration
  credentials shown on the sign-in page.

Basic auth via nginx:

```bash
sudo htpasswd -c /etc/nginx/.htpasswd demo
```

```nginx
location / {
    auth_basic           "Restricted";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass           http://127.0.0.1:8000;
    # ... the proxy_set_header lines from above
}
```

---

## Set the server's timezone

The portal stores naive local datetimes, which works because Ghana is on GMT
year-round with no daylight saving. **A server in another timezone will shift
every rostered time, attendance stamp and audit entry.**

```bash
sudo timedatectl set-timezone Africa/Accra   # or UTC, which is identical
timedatectl                                   # confirm
```

On Windows, set the timezone to **(UTC) Coordinated Universal Time** or
**(UTC+00:00) Monrovia, Reykjavik**.

Do this **before** running `seed.py`, otherwise the seeded rosters are built
around the wrong "today".

---

## Checking it works

```bash
curl http://your-server:8000/healthz
```

```json
{"status":"ok","database":"ok","system":"Intelligent Hospital Workforce Scheduling System"}
```

Returns 503 if the database is unreachable. Suitable for uptime monitoring.

Then, **from a different machine**, open `http://your-server:8000/` and sign in
as `TH-ADM-001` / `Password123`. Testing from the server itself will not catch
the secure-cookie problem described above.

---

## Updating a running deployment

```bash
sudo systemctl stop ihwss
# copy the new files over, keeping .env and instance/hospital.db
.venv/bin/pip install -r requirements.txt -r requirements-prod.txt
sudo systemctl start ihwss
```

`instance/hospital.db` holds all the data. Back it up by copying that one file;
the application can be stopped, the file copied, and the application started
again in a couple of seconds.

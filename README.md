# HouseBoard

Lightweight local-first household dashboard for chores, tasks, and shared status screens.

## Run Locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

Open `http://localhost:8000`.

On first launch, HouseBoard asks for an admin username, shows a TOTP QR code, and requires one valid authenticator code before creating the admin account. Admin users can add more users from `/users`.

## Configuration

Environment variables:

```bash
HOUSEBOARD_SECRET_KEY=replace-this-before-exposing
HOUSEBOARD_DATABASE_URL=sqlite:////absolute/path/houseboard.db
HOUSEBOARD_RESET_HOUR=4
HOUSEBOARD_RESET_MINUTE=0
HOUSEBOARD_TOTP_ISSUER=HouseBoard
```

Daily chores reset to incomplete at 4:00 AM local household time by default. The scheduler checks once per minute.

Projects are task-centered: each task can be assigned to an existing project name or create a new project name from the task form. Dashboard project metrics are calculated from the task data, so there is no separate project setup step.

## Raspberry Pi Notes

The app runtime is cross-platform Python. The files in `deploy/` are Linux/systemd examples for Pi OS Lite or another systemd-based Linux install.

If you expose HouseBoard outside your LAN, set a strong `HOUSEBOARD_SECRET_KEY` and put it behind HTTPS, preferably with a reverse proxy or VPN rather than raw port forwarding.

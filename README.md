# 📍 Geo Tracker

# go-tracker

A local web app that combines **live GPS location sharing**, **IP geolocation**, and **phone number lookup** in one dashboard with an interactive map (Leaflet + OpenStreetMap).

Built with Python + Flask + SQLite. No API keys required. Runs locally, single user, no login.

## Features

| Feature | Description |
|---|---|
| 🔴 **Live GPS sharing** | Create a share link on the dashboard → open it on any device → the visitor's live position streams to the map every 5 seconds, with an accuracy circle and a movement trail |
| 🌐 **IP geolocation** | Enter any IP → country, city, ISP, ASN, timezone + marker on the map (via free `ipwho.is` API) |
| 📱 **Phone lookup** | Enter a phone number → operator, country, timezone, number type (via the `phonenumbers` library) |

The dashboard shows all GPS sessions with live/stale status, last-seen time, copy-link and delete buttons, and updates **instantly via Server-Sent Events (SSE)** — no polling, so markers move the moment a fix arrives.

## Quick start

```bash
cd geo-tracker
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** — you'll see the dashboard. Create a share link, open it in another tab/phone browser, press **Start sharing**, and watch the marker move on the map.

### Run tests

```bash
pytest -q
```

## Automatic deployment (GitHub Actions)

Every push to `main` deploys over SSH. The current failures stop in **Configure
SSH**, which means a required GitHub Actions secret has not been set (the server
has not been contacted yet).

On the Ubuntu server, run the one-time bootstrap as root (first review the
repository URL in the script):

```bash
git clone https://github.com/wisnufebriramadhan/go-tracker.git /tmp/go-tracker
sudo /tmp/go-tracker/deploy/bootstrap-server.sh
sudoedit /opt/go-tracker/.env
```

Set a real `SECRET_KEY` in `/opt/go-tracker/.env`. Then add these repository
secrets in **GitHub → Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | Server IP address or DNS name |
| `DEPLOY_USER` | `deployer` (or the SSH user configured on the server) |
| `DEPLOY_SSH_KEY` | Private key for that user, including its header/footer |
| `DEPLOY_KNOWN_HOSTS` | Output of `ssh-keyscan -H <server-host>` run from a trusted machine |

Add the matching public key to `/home/deployer/.ssh/authorized_keys` on the
server (for example, generate it locally with `ssh-keygen -t ed25519 -f
go-tracker-deploy -C go-tracker-deploy`). The user may run only
`/usr/local/sbin/deploy-go-tracker` with `sudo`.
After committing this setup, use **Actions → Deploy → Run workflow**, or push
to `main`. The workflow now reports the exact missing secret instead of an
unexplained exit code.

## Testing from a real phone (HTTPS required)

Browsers **only allow GPS access on secure (HTTPS) contexts** or `localhost`. Testing on the same computer works out of the box, but a phone on your LAN needs HTTPS. The easiest way is a free [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/):

```bash
# terminal 1: run the app
flask --app app run --host 0.0.0.0 --port 5000

# terminal 2: expose it with a public HTTPS URL
cloudflared tunnel --url http://localhost:5000
```

Use the `https://…trycloudflare.com` URL in the dashboard's share links, open it on the phone, and approve the location prompt. The page itself is transparent: it tells the visitor their location is being shared and offers a **Stop** button.

## API overview

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Dashboard (map + tabs) |
| `/share/<token>` | GET | Consent page that streams GPS |
| `/api/sessions` | GET/POST | List sessions / create a session |
| `/api/sessions/<token>/location` | POST | Receive a GPS fix `{lat, lon, accuracy}` |
| `/api/sessions/<id>` | GET/DELETE | Session detail + trail / delete session |
| `/api/events` | GET | SSE stream — pushes session updates to the dashboard instantly |
| `/api/ip/<address>` | GET | IP geolocation via ipwho.is |
| `/api/phone/<number>` | GET | Phone info via phonenumbers |

## Project structure

```
geo-tracker/
├── app.py           # Flask routes + pages
├── db.py            # SQLite schema + helpers
├── services.py      # ipwho.is + phonenumbers wrappers
├── templates/       # index.html (dashboard), share.html (GPS page)
├── static/          # app.js, share.js, style.css
└── tests/           # pytest suite
```

## ⚖️ Consent & legal note

This tool is a **consent-based location tracker**, like Find My Device or a "share my location" app:

- The share page explicitly tells the visitor their location is being sent, requires the browser's permission prompt, and has a visible **Stop** button.
- It is **not** a covert tracking tool.

Covertly tracking a person's location without their knowledge or consent is illegal in many jurisdictions. In Indonesia it can violate the **UU PDP (Law No. 27/2022 on Personal Data Protection)** and **UU ITE**. Use this tool only with the clear consent of the person whose location you track, or on devices you own.

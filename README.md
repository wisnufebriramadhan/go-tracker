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

Open **http://127.0.0.1:5000** — you'll be asked to sign in (default: `admin` / `admin123`, override with `ADMIN_USER` / `ADMIN_PASS` in `.env`). Create a share link, open it in another tab/phone browser, press **Start sharing**, and watch the marker move on the map.

Share pages (`/share/<token>`) and their APIs stay **public by design** — that's the point of a share link — while the dashboard and its APIs require login.

## Camera photos on the dashboard

From the share page, the visitor can enable the camera and take a photo (with the usual browser permission prompt). Photos are uploaded to the dashboard where they appear as **thumbnail markers on the map** (with a larger preview in the popup), in the session card as a 📷 count, and in the **Photos** gallery modal. When GPS is active, each capture is tagged with the current coordinates so it lands exactly where it was taken.

## Deploy (one command)

The app is deployed at **https://wisnufebri.cloud** (nginx → gunicorn → Flask on the server).

```bash
cd geo-tracker/.deploy
./deploy.sh        # rsync + restart + health check
```

First-time server setup (already done):

1. SSH in once, install an SSH key (`cat id_ed25519.pub >> ~/.ssh/authorized_keys`) and fix home dir permissions (`chmod go-w ~`).
2. Write a valid `.env` with real newlines: `FLASK_ENV`, `SECRET_KEY`, `DB_PATH`, `MEDIA_DIR`, `ADMIN_USER`, `ADMIN_PASS`.
3. The systemd unit `go-tracker.service` runs gunicorn on `127.0.0.1:8010`; nginx/Cloudflare serves it publicly. Restarting is done by killing gunicorn — systemd's `Restart=always` respawns it, no root needed.

`.deploy/` is gitignored — it holds the SSH key and is not meant to be shared.

### Run tests

```bash
pytest -q
```

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

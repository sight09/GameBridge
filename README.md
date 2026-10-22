# GameBridge Ethiopian Esports Ladder Platform 🇪🇹🎮

[![Test Suite](https://img.shields.io/badge/pytest-23%20passed-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-teal.svg)]()
[![Localization](https://img.shields.io/badge/i18n-English%20%7C%20Amharic%20%7C%20Afaan%20Oromo-gold.svg)]()

> **GameBridge 250 Innovation Challenge Prototyping Gate Delivery**  
> A mobile-first, Telegram-integrated but browser-independent esports ladder platform engineered for Ethiopian gamers, budget Android hardware, low bandwidth, and community gaming hubs.

---

## 1. Product Overview

GameBridge connects grassroots Ethiopian gamers to an auditable competitive pathway:
**Grassroots Daily Cups → City Rankings → Regional Championships → National Finals → Global Scouting**.

### Key Features
- **Multilingual from Day 1:** Full interface and validation available in **English**, **Amharic (አማርኛ)**, and **Afaan Oromo**.
- **Telegram Mini App + Browser Independent:** Play through Telegram Mini Apps, standard mobile browsers, or desktop without requiring Telegram.
- **Official API Integrations (Tier A):** Verified player ratings and automated match result verification via official Lichess and Chess.com APIs.
- **Append-Only Match Integrity:** Match outcomes and dispute resolutions are append-only business events with full audit logging.
- **Platform Elo Rating:** Custom calibrated rating system ($K=40$ for provisional matches, $K=24$ for established players) with Bronze, Silver, Gold, Platinum, and Diamond tiers.
- **Single-Elimination Bracket Engine:** Automatic power-of-two padding with Byes and automated bracket advancement.
- **Hub-to-City Qualification Pathways:** Link a local tournament to an open next-stage event; the top four finishers are automatically registered when the bracket completes, with places reserved from public registration.
- **Public Gamer Passport:** Shareable competitor profiles at `/p/{public_identifier}` with Pillow-generated downloadable PNG share cards.
- **Hub Captain Dashboard:** Municipal hub management, tournament creation, and dispute resolution queue.
- **Isolated Demo Dataset & Safe Reset:** Over 40 fictional players, 3 gaming hubs, and historical tournament matches that can be safely wiped and re-seeded without deleting real pilot user records.

---

## 2. Technology Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.x (Asyncio), Alembic, Pydantic 2.x, HTTPX
- **Database:** PostgreSQL (production), SQLite via `aiosqlite` (local development)
- **Telegram:** aiogram 3 (Webhook mode integrated into FastAPI), Telegram WebApp cryptographic initData validation
- **Frontend:** Jinja2 templates, HTMX, lightweight mobile-first CSS (< 60 KB uncompressed), vanilla JS, Pillow (PNG share card generator)
- **DevOps:** Docker, Docker Compose, Makefile, Environment configuration, PWA Service Worker

---

## 3. Quickstart: Local Development

### Prerequisites
- Python 3.12+
- Git

### 1. Clone & Set Up Virtual Environment
```bash
git clone https://github.com/juagro2020-art/GameBridge_dev.git
cd GameBridge_dev
python -m venv venv

# Windows PowerShell:
.\venv\Scripts\Activate.ps1

# Linux / macOS:
source venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
# Or:
pip install -e .
```

### 3. Initialize Database & Seed Demo Data
```bash
python -m scripts.seed_demo
```
This runs table migrations, provisions default games (Lichess, Chess.com) and milestone badges, and seeds 3 hubs, 40 fictional Ethiopian players, a finished cup, and an open cup. The captain dispute queue starts clear; real disputes are added when players report conflicting results.

### 4. Start the Application
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Open your browser at **`http://localhost:8000`**.

---

## 4. Docker Compose

Run the containerized application with PostgreSQL:
```bash
docker compose up -d --build
```
The application will be available at `http://localhost:8000`.

---

## 5. Judge Walkthrough: 5-Minute Evaluation Tour

Visit **`/judge`** for an interactive, guided walkthrough designed for innovation challenge evaluators:

1. **Minute 1 — Discover & Register:** Register a new competitor or switch between English and Amharic with one click.
2. **Minute 2 — Official Account Verification:** Link a Lichess account, receive a random bio code (e.g., `GB-4F7K`), and verify profile ownership via official API.
3. **Minute 3 — Enter a Tournament:** Join the live *Addis Ababa City Cup #2*, inspect the seeded single-elimination bracket, and enter a Match Room.
4. **Minute 4 — Match Verification:** Open the direct completed Match Room link to inspect a persisted result event. New live reports require a real Lichess game URL and both players' verified accounts; no simulated success is shown.
5. **Minute 5 — Trust, Captaincy & Passports:** Sign in as `captain_bole` and inspect the clear dispute queue at `/captain`. Real disputes appear there when players report conflicting results. Then inspect the public Gamer Passport (`/p/et-almaz_a`) and share card.

### Tournament qualification pathways

A hub captain can configure a pathway from a tournament detail page by selecting an open next-stage tournament for the same game and mode. The top four finishers advance automatically when the source bracket completes; tournaments with fewer than four entrants advance all entrants. The next stage reserves four places for qualifiers and cannot be started while any linked qualifying tournament is unfinished. Finishers are ordered by champion, furthest completed round, then seed.

To see a prefilled mock example locally, start the app with demo mode enabled and open `/judge`. The finished **Bole Grassroots Rapid Cup #1** is linked to **Addis Ababa City Finals (Demo)**. Its four demo qualifiers (Almaz, Selam, Yared, and Tigist) appear on the source cup and as qualified entrants in the city final; four other fictional players are also registered, leaving four open places. All players and results in this walkthrough are fictional demonstration data.

### Quick Evaluation Logins
On `/login`, one-click buttons allow instant evaluation as:
- **Competitor:** `desta_k` (password: `player123`)
- **Hub Captain:** `captain_bole` (password: `captain123`)
- **Super Administrator:** `admin` (password: `admin123`)

---

## 6. Testing & Quality Gates

Run the comprehensive automated test suite (ratings, brackets, byes, auth, adapters, disputes, permissions, and e2e journeys):
```bash
pytest -v
```
All repository tests pass with mocked external API fixtures. Existing Pydantic and `datetime.utcnow()` deprecation warnings remain.

---

## 7. Safe Demo Dataset Management

To reset the demonstration dataset without touching real pilot players:
```bash
python -m scripts.reset_demo
```
Or sign in as the super-admin and use **Reset Demo Dataset** on `/judge`. Reset requires server-side super-admin authorization; the safe wipe targets only records with `is_demo = True`.

---

## 8. Critical-Path Network Performance

Measured from the current static files:
- `style.css`: 26,712 bytes raw / 5,809 bytes gzip
- `app.js`: 3,160 bytes raw / 1,329 bytes gzip
- `htmx.min.js`: 48,101 bytes raw / 15,665 bytes gzip
- **Static assets total:** 77,973 bytes raw / 22,803 bytes when gzip-compressed. Local Uvicorn does not enable HTTP compression, so actual static transfer is the raw total, still below 200 KB. Lighthouse was not available in this environment.

---

## 9. Security & Minor Safeguards

- **No Real Money:** All tournaments are free; no gambling, pay-to-win, or cash entry fees.
- **Telegram Cryptographic Validation:** Server validates Telegram WebApp `initData` using official HMAC-SHA256 signature verification and rejects expired auth tokens.
- **Append-Only Event Ledger:** Match results and captain corrections are logged as immutable business events with prior event pointers and reasons.
- **Private Match Evidence:** Uploaded screenshots are stored with cryptographically random filenames and restricted from public viewing.
- **Hub-Scoped Captain Actions:** Captain tournament operations, dispute queues, and evidence reads require assigned hub membership; super-admin access remains platform-wide.
- **Data Minimization:** No government identity numbers, payment cards, or exact birth dates collected.

---

## 10. License & Innovation Attribution
Developed for the **GameBridge 250 Innovation Challenge Prototyping Gate**.
Licensed under the MIT License.

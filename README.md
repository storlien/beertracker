# BeerTracker Backend Service

Python background service that syncs Zettle card purchase transactions to Firebase Firestore. The website reads directly from Firestore — there is no REST API surface.

---

## Architecture

- **Pure background worker** (no REST API)
- **Zettle API** → Firestore `cards/` and `info/state` collections
- **Leaderboard** is pre-computed in memory and written to `leaderboard/top100`
- **Users** are created/managed by the website frontend, not this service
- **Firebase Firestore** is the single source of truth for the website
- Runs as a **single Docker container**

## Data Model

| Collection | Document ID | Fields |
|---|---|---|
| `cards` | `1234567890` (first 6 + last 4) | `sum` (float), `linkedUserId` (optional), `createdAt`, `updatedAt` |
| `info` | `state` | `lastPurchaseHash` (string), `lastSyncAt` (timestamp), `totalPurchasesSynced` (int) |
| `users` | auto-ID | Managed by website (not touched by this service) |
| `leaderboard` | `top100` | `entries[]` (pre-computed top 100), `updatedAt` |

> **Firestore Rules**: Update your rules to allow read access on the `leaderboard` collection:
> ```
> match /leaderboard/top100 { allow read: if true; }
> ```

## Adaptive Sync Rate

The backend automatically adjusts how often it polls Zettle:

- **Active mode** (bar is busy): sync every 10 seconds
- **Hibernate mode** (no activity for 60 minutes): sync every 3 minutes

A single purchase flips the service back to active mode immediately. The in-memory leaderboard cache detects card changes via Firestore `on_snapshot` listeners, so the frontend always sees fresh data without causing reads.

## Leaderboard Pre-computation

Instead of every browser downloading all cards and users to compute the top 100 locally, the backend:

1. Keeps the full `cards` and `users` collections in memory via Firestore listeners.
2. After every sync (or any card/user change), recomputes the leaderboard entirely in memory (zero Firestore reads).
3. Writes a single `leaderboard/top100` document.
4. The frontend listens to just this one document.

This reduces frontend Firestore reads from **N browsers × (150 cards + all users)** per sync down to **N browsers × 1 document**.

## Tech Stack

- Python 3.12 + `uv` for dependency management
- `httpx` — modern HTTP client with connection pooling
- `tenacity` — retry logic with exponential backoff
- `pydantic-settings` — type-safe environment configuration
- `structlog` — structured JSON logging
- `apscheduler` — background job scheduling
- `firebase-admin` — Firestore client
- `click` — CLI commands for management

## Environment Variables

Copy `.env.example` to `.env` and fill in your **Zettle assertion key**:

```bash
cp .env.example .env
chmod 600 .env        # restrict permissions — contains secrets
# edit .env and set BEERTRACKER_ZETTLE_ASSERTION_KEY
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `BEERTRACKER_ZETTLE_ASSERTION_KEY` | ✅ | — | LaBamba Zettle JWT assertion key |
| `BEERTRACKER_FIREBASE_SERVICE_ACCOUNT_PATH` | | `firebase-service-account.json` | Path to Firebase service account JSON |
| `BEERTRACKER_SYNC_INTERVAL_ACTIVE` | | `10` | Seconds between syncs when bar is active |
| `BEERTRACKER_SYNC_INTERVAL_HIBERNATE` | | `180` | Seconds between syncs when idle |
| `BEERTRACKER_SYNC_HIBERNATE_AFTER_MINUTES` | | `60` | Minutes of no activity before hibernation |
| `BEERTRACKER_TOKEN_REFRESH_MINUTES` | | `30` | Minutes between token refreshes |
| `BEERTRACKER_LOG_LEVEL` | | `INFO` | Logging level |

**⚠️ Security**: `.env` and `firebase-service-account.json` are both in `.gitignore` and `.dockerignore`. Never commit them.

## Running Locally

```bash
# Install dependencies
uv sync

# Run the background sync worker (reads .env automatically)
uv run python -m beertracker
```

## CLI Commands

```bash
# Inspect Firestore collections
uv run beertracker-cli inspect --collection cards

# Manually recompute leaderboard (after seeding cards, etc.)
uv run beertracker-cli compute-leaderboard
```

## Running Tests

```bash
uv run pytest tests/ -v
```

## Docker

The `Dockerfile` is a multi-stage build using Python 3.12 slim with a non-root user (`appuser`). A health check verifies `info/state.lastSyncAt` is within 10 minutes.

```bash
docker build -t beertracker .
docker run -d \
  -e BEERTRACKER_ZETTLE_ASSERTION_KEY=<key> \
  -v $(pwd)/firebase-service-account.json:/app/firebase-service-account.json:ro \
  beertracker
```

## Directory Layout

```
beertracker/
├── api/                  # Zettle API client + token manager
├── core/                 # Domain models, validators, mappers, leaderboard logic
├── firebase/             # Firestore client + repositories
├── services/             # In-memory leaderboard cache + repository
├── __main__.py           # Service entry point
├── cli.py                # Click CLI for management
├── config.py             # Pydantic Settings
├── logging_setup.py      # structlog JSON logging
├── scheduler.py          # APScheduler sync worker
├── healthcheck.py        # Docker health check script
tests/                    # Unit tests
Dockerfile
docker-compose.yml
pyproject.toml            # uv project config
```

## Migration Notes from Java

| Java Class | Python Module | Notes |
|---|---|---|
| `TokenGetter.java` | `api/token_manager.py` | OAuth refresh with `httpx` + `tenacity` retries |
| `CallAPI.java` + `PurchaseReader.java` | `api/client.py` | Pagination via `lastPurchaseHash`, robust retry |
| `PurchaseMapper.java` | `core/mappers.py` | Filters `IZETTLE_CARD`, converts øre→kr, aggregates by card |
| `Person.java` validators | `core/validators.py` | Card format, name format, amount validation |
| `Filehandler.java` | `cli.py` management commands | Legacy file handling (not needed for new installs) |
| `Card.java` / `User.java` | `core/models.py` | Dataclasses for domain entities |
| `FirestoreService.java` | `firebase/client.py` | Singleton Firebase Admin init |
| `CardRepo.java` / `InfoRepo.java` | `firebase/repositories.py` | Firestore CRUD + atomic increment |
| `Service.java` timer logic | `scheduler.py` | APScheduler with adaptive sync + cache |

---

## Security

- `firebase-service-account.json` is in `.gitignore` and `.dockerignore` — **never commit it**
- All secrets are injected via environment variables
- The container runs as non-root (`appuser`)
- No secrets are logged by the structured logger

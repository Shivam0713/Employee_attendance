# PeopleLedger

**Employee attendance and workforce analytics, in one workspace.**

PeopleLedger is an employee attendance system built with FastAPI and MongoDB. Administrators manage employees and attendance records; employees can view their own attendance. Reports summarize attendance, working hours, late arrivals, and department trends.

## Features

- Role-based administrator and employee access
- Employee management and attendance punch-in / punch-out
- ABSENT and LEAVE records without punch times
- Auditable attendance corrections
- Monthly summaries, department analytics, late-arrival leaderboard, and attendance trends
- MongoDB indexes and aggregation pipelines for reporting

## Quick start

Requirements: Python 3.11+ and MongoDB 6.0+.

```bash
python -m venv .venv
```

Activate the virtual environment, then install dependencies and configure the database:

```bash
pip install -r requirements.txt
```

Set `MONGO_URI` and `MONGO_DB` in your environment, or create a local `.env` file in the project root:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
COOKIE_SECURE=false
```

Start the API and dashboard:

```bash
uvicorn app.main:app --reload --port 8000
```

Create the first administrator in a second terminal:

```bash
python bootstrap_admin.py
```

Then open **http://localhost:8000**. Interactive API documentation is available at **http://localhost:8000/docs**. Indexes are created automatically at startup.

## Configuration and security

Real environment variables take precedence over values in `.env`. Keep `.env` out of Git and never commit database credentials. Set `COOKIE_SECURE=true` when serving the application over HTTPS. Sample data dumps and virtual environments are not included in this repository.

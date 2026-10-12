# Employee Attendance and Analytics API

FastAPI, MongoDB, and a browser dashboard for employee attendance management and analytics.

Install dependencies with `pip install -r requirements.txt`, configure `MONGO_URI` and `MONGO_DB` in your environment (or a local, uncommitted `.env`), then run `uvicorn app.main:app --port 8000` from the repository root. MongoDB 6.0+ is required; indexes are created at startup.

Create the first administrator with `python bootstrap_admin.py`, then open `http://localhost:8000`. Admins can record attendance as PRESENT, WFH, ON_DUTY, ABSENT, or LEAVE; non-working records have no punch times. Sample data dumps are not included in this repository.

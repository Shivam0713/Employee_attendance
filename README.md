# Employee Attendance and Analytics API

FastAPI and MongoDB attendance service with a browser dashboard served from the same application.

## Configure MongoDB

The local `.env` file stores the database connection and is excluded from Git. Update these values to connect your MongoDB instance:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

For MongoDB Atlas, set `MONGO_URI` to your cluster connection string, including your database user, password, and cluster host. Keep the local `.env` file private and out of Git.

## Start the app

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** for the dashboard. The interactive API documentation is at **http://localhost:8000/docs**. The app creates its required MongoDB indexes at startup and serves the dashboard and API from the same origin.

To load the provided sample data into the configured database, run:

```powershell
python sample_seed.py
```

MongoDB 6.0 or newer is required. Set real process environment variables to override values from `.env`.

## Dashboard features

- Overview cards and recent attendance
- Employee creation and department filtering
- Punch-in and punch-out forms
- Attendance search and filters
- Monthly department summary and late-arrival leaderboard
# Authentication and roles

Install the updated dependencies with `pip install -r requirements.txt`. Add `COOKIE_SECURE=false` to the local `.env` when using plain `http://127.0.0.1`; set it to `true` behind HTTPS in deployment. Keep `.env` private and never commit it.

Create the first administrator once with `python bootstrap_admin.py`. The script reads `MONGO_URI` and `MONGO_DB` from `.env`, prompts for the email/password without echoing the password, and refuses to create a second bootstrap admin. Then start the API with `uvicorn app.main:app --reload`. Visit the app and sign in.

Administrators add employee records and set a temporary initial sign-in password in the Add employee form. Employee accounts sign in with the employee email and are limited to their own attendance history, monthly summary, and punch actions. There is no public registration. Existing employees created before auth was added need an account provisioned by an administrator; use the admin-only `POST /admin/employee-accounts` route with `{ "emp_code": "EMP0001", "password": "a-long-temporary-password" }`.

Sessions are opaque, stored server-side in MongoDB, and expire after 12 hours. Passwords are Argon2 hashes. Cookie-authenticated mutations require the CSRF token sent by the same-origin frontend. The older organization-wide APIs now require administrator sessions. `/health` remains public and does not expose database credentials.

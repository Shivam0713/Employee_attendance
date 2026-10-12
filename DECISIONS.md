# Implementation decisions

1. **Indexes.** A unique `employees.emp_code` index protects employee identity. The unique `(emp_code, date)` attendance index enforces one record per employee and day and makes punch-in races safe. I use `(date, emp_code)` for the attendance list sort, `(emp_code, date)` for employee history and monthly lookups, `(status, date)` for status filters, `(department, joined_on)` for department reports and trends, and `joined_on` for the department summary's eligible employee scan. I did not add a separate status-only index because the status/date compound index already has that prefix.

2. **Punch-in race.** Both requests may check the employee, but neither performs a check-then-insert on the attendance record. MongoDB's unique `(emp_code, date)` index accepts one insert. The other insert raises duplicate key, which the API returns as 409. The successful call returns 201.

3. **Ties.** The pipeline uses `$rank` on total late minutes, then filters by rank. If two people tie at the cutoff rank, both are returned. Rows are then ordered by late minutes and employee code for stable display.

4. **Headcount.** Department summary starts from eligible employee documents and looks up that month's logs. Employees without logs remain in the pipeline, so they count toward headcount while contributing no attendance values.

5. **At 100x scale.** I would precompute monthly summaries and trend inputs, refresh them incrementally as records change, and measure query plans and index costs against production-like data before adding more indexes.

6. **Authentication.** Admin and employee login accounts live in separate `auth_users` and `auth_sessions` collections so the assignment's `employees` and `attendance_logs` document formats stay intact. Passwords use Argon2; the browser receives an opaque HttpOnly session cookie while MongoDB stores only its SHA-256 hash. Session authorization resolves the role and employee link from MongoDB on each request. A CSRF cookie/header pair protects mutations. The first admin is created by the interactive `bootstrap_admin.py` command; there is no public registration.

7. **Employee scope.** Employee attendance APIs derive `emp_code` from the authenticated account and query only that code. Organization-wide APIs remain available to administrators and reject employee sessions server-side; frontend visibility is only a usability layer.

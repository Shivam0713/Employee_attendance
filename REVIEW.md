# Starter review

| # | Where | Defect and how to notice it | Fix |
|---|---|---|---|
| 1 | `health` | Always returned 200, even with MongoDB down. A database outage was not visible to readiness checks. | Ping MongoDB and return 503 on failure. |
| 2 | `create_employee` | The duplicate lookup followed by insert was race-prone; concurrent requests could create duplicate codes. | Enforce a unique index and translate duplicate-key errors to 409. |
| 3 | `create_employee` | `created_at` was a naive datetime and the response did not convert instants to epoch milliseconds. | Store UTC and serialize to epoch milliseconds. |
| 4 | `EmployeeIn` | Codes, email, names, departments, shift times, joining dates, and equal start/end shifts were not validated against the contract. | Add constrained Pydantic fields and date/shift validation. |
| 5 | `list_employees` | Page 1 skipped one full page because it used `page * page_size`; total ignored the department filter. | Use `(page - 1) * page_size` and count with the same query. |
| 6 | `list_employees` | Results were not sorted by `emp_code`, and page values were not bounded. | Sort consistently and validate page and page size. |
| 7 | `compute_late_minutes` / `punch_in` | The timestamp was interpreted in the host's local timezone, not UTC then IST; overnight records used the wrong attendance day and shift start. | Convert instants explicitly, derive the IST attendance date, and calculate against that shift date. |
| 8 | `compute_late_minutes` / punch-in model | Seconds-looking, float, string, and out-of-range timestamps were not reliably rejected; non-presence status values could be accepted. | Validate strict epoch-millisecond integers and restrict punch-in statuses. |
| 9 | `compute_work_hours` | Python `round` uses ties-to-even, not the required half-up rounding. | Use `Decimal` with `ROUND_HALF_UP`. |
| 10 | `compute_overtime` | It did not handle overnight shift end dates, used incompatible naive/aware datetime values, and counted overtime below the 30-minute minimum. | Build the shift end in IST on the correct day and apply the 30-minute threshold. |
| 11 | `punch_in` | A missing employee caused a 500; duplicate check then insert allowed races; the response exposed an `id` that is not in the contract. | Return 404, rely on a unique `(emp_code, date)` index for concurrency, and serialize only contract fields. |
| 12 | `punch_in` | The timestamp used system-local time, did not truncate to whole seconds, and did not account for the overnight attendance-date rule. | Normalize to UTC whole seconds and derive the date from the employee's IST shift. |
| 13 | `list_attendance` | It loaded and sorted the entire collection in Python, which does not scale to 100,000 records. | Count in MongoDB and use indexed sort, skip, and limit. |
| 14 | `list_attendance` | It returned an `id` derived from MongoDB `_id`, contrary to the natural-key API contract; filters and date range were not validated. | Omit `_id`, retain `(emp_code, date)`, and validate filters and pagination. |
| 15 | Missing endpoints | Punch-out, regularization, all analytics, and explain were absent. | Implemented the remaining endpoints with atomic updates and MongoDB pipelines. |

## Reviewed and retained

- The starter's late-minute formula used the right ten-minute strict grace comparison and measured from shift start for ordinary same-day shifts; its timezone and overnight context was the defect.
- Using a unique attendance key is necessary because an application-side existence check alone cannot guarantee one record under concurrent requests.

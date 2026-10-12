const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const PRESENCE = new Set(['PRESENT', 'WFH', 'ON_DUTY']);
const state = { employees: [], attendance: [], todayAttendance: [], page: 1, pageSize: 100, toastTimer: null, profile: null };

function todayIST() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
}
function msFromInput(value) {
  return value ? new Date(value).getTime() : undefined;
}
function inputDateTime(ms) {
  if (ms == null) return '';
  const date = new Date(ms);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function dateLabel(value) {
  if (!value) return '—';
  const [y, m, d] = value.split('-').map(Number);
  return new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, d)));
}
function timeLabel(value) {
  if (value === null || value === undefined) return '—';
  return new Intl.DateTimeFormat('en', { hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kolkata' }).format(new Date(value));
}
function initials(name = '') { return name.trim().split(/\s+/).slice(0, 2).map(part => part[0] || '').join('').toUpperCase() || '??'; }
function safe(value = '') { return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]); }
function humanStatus(status) { return ({ PRESENT: 'Present', WFH: 'Work from home', ON_DUTY: 'On duty', ABSENT: 'Absent', LEAVE: 'Leave' })[status] || status || '—'; }
function badge(status) { return `<span class="status-badge badge-${safe((status || '').toLowerCase())}">${safe(humanStatus(status))}</span>`; }

async function request(url, options = {}) {
  const csrf = document.cookie.split('; ').find(item => item.startsWith('northstar_csrf='))?.split('=').slice(1).join('=');
  const response = await fetch(url, { credentials: 'same-origin', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(csrf && options.method && options.method !== 'GET' ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}), ...(options.headers || {}) } });
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(', ') : data.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return data;
}
function notify(message, isError = false) {
  const toast = $('#toast');
  const activeDialog = document.querySelector('dialog[open]');
  const destination = activeDialog || document.body;
  if (toast.parentElement !== destination) destination.append(toast);
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.classList.add('show');
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove('show'), 3200);
}
function setConnection(online) {
  const el = $('#connection-state');
  el.classList.toggle('online', online);
  el.classList.toggle('offline', !online);
  el.innerHTML = `<i></i> ${online ? 'API connected' : 'API unavailable'}`;
}
function employeeByCode(code) { return state.employees.find(employee => employee.emp_code === code); }
function employeeIdentity(code) {
  const employee = employeeByCode(code);
  return `<div class="employee-cell"><span class="mini-avatar">${safe(initials(employee?.name || code))}</span><span><b>${safe(employee?.name || 'Unknown employee')}</b><small>${safe(code)}</small></span></div>`;
}

async function loadEmployees() {
  const data = await request('/employees?page=1&page_size=100');
  state.employees = data.items || [];
  $('#metric-employees').textContent = data.total;
  $('#employee-nav-count').textContent = data.total;
  renderEmployees();
  populateEmployeeControls();
}
async function loadAttendance() {
  const today = todayIST();
  const [data, todayData] = await Promise.all([
    request('/attendance?page=1&page_size=100'),
    request(`/attendance?date_from=${today}&date_to=${today}&page=1&page_size=100`),
  ]);
  state.attendance = data.items || [];
  state.todayAttendance = todayData.items || [];
  renderAttendance();
  renderOverviewAttendance();
}
async function refreshAll() {
  try {
    await request('/health');
    setConnection(true);
    await Promise.all([loadEmployees(), loadAttendance()]);
    $('#today-label').textContent = new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric', timeZone: 'Asia/Kolkata' }).format(new Date()).toUpperCase();
  } catch (error) {
    setConnection(false);
    notify(`Could not load workspace: ${error.message}`, true);
  }
}
function renderEmployees() {
  const query = ($('#employee-search')?.value || '').trim().toLowerCase();
  const department = $('#department-filter')?.value || '';
  const filtered = state.employees.filter(employee => (!department || employee.department === department) && (!query || `${employee.name} ${employee.emp_code} ${employee.email}`.toLowerCase().includes(query)));
  const target = $('#employees-table');
  if (!filtered.length) target.innerHTML = `<tr><td colspan="5" class="empty-state">${state.employees.length ? 'No employees match this search.' : 'No employees yet. Add your first teammate to get started.'}</td></tr>`;
  else target.innerHTML = filtered.map(employee => `<tr><td>${employeeIdentity(employee.emp_code)}</td><td>${safe(employee.department)}</td><td>${safe(employee.shift_start)} – ${safe(employee.shift_end)} <small class="muted-text">IST</small></td><td>${dateLabel(employee.joined_on)}</td><td><div class="employee-row-actions"><button class="table-action" data-employee="${safe(employee.emp_code)}">Details</button><button class="table-action table-action-danger" data-delete-employee="${safe(employee.emp_code)}">Delete</button></div></td></tr>`).join('');
  $('#employee-footer').textContent = `Showing ${filtered.length} of ${state.employees.length} employees`;
  const departments = [...new Set(state.employees.map(employee => employee.department))].sort();
  const current = $('#department-filter')?.value || '';
  $('#department-filter').innerHTML = `<option value="">All departments</option>${departments.map(dept => `<option value="${safe(dept)}">${safe(dept)}</option>`).join('')}`;
  $('#department-filter').value = current;
}
function populateEmployeeControls() {
  const opts = state.employees.map(employee => `<option value="${safe(employee.emp_code)}">${safe(employee.name)} · ${safe(employee.emp_code)}</option>`).join('');
  $$('.employee-select').forEach(select => { const selected = select.value; select.innerHTML = `<option value="">Choose an employee</option>${opts}`; select.value = selected; });
  const departments = [...new Set(state.employees.map(employee => employee.department))].sort();
  const select = $('#analytics-department');
  const selected = select.value;
  select.innerHTML = `<option value="">All departments</option>${departments.map(dept => `<option value="${safe(dept)}">${safe(dept)}</option>`).join('')}`;
  select.value = selected;
}
function renderOverviewAttendance() {
  const logs = state.todayAttendance;
  const present = logs.filter(record => PRESENCE.has(record.status));
  const late = logs.filter(record => Number(record.late_minutes || 0) > 0);
  const denominator = state.employees.length;
  const percent = denominator ? Math.min(100, Math.round((present.length / denominator) * 100)) : 0;
  $('#metric-present').textContent = present.length;
  $('#metric-late').textContent = late.length;
  $('#metric-departments').textContent = new Set(state.employees.map(employee => employee.department)).size;
  $('#present-caption').textContent = `Of ${denominator} team members`;
  $('#attendance-rate').textContent = `${percent}%`;
  const deg = Math.round(percent * 3.6);
  $('#attendance-donut').style.background = `conic-gradient(#7766e8 ${deg}deg, #e7e4fb ${deg}deg, #f2f3f6 360deg)`;
  $('#legend-present').textContent = present.length;
  $('#legend-late').textContent = late.length;
  $('#legend-unrecorded').textContent = Math.max(0, denominator - logs.length);
  const recent = [...state.attendance].sort((a, b) => b.date.localeCompare(a.date) || a.emp_code.localeCompare(b.emp_code)).slice(0, 5);
  $('#recent-table').innerHTML = recent.length ? recent.map(record => `<tr><td>${employeeIdentity(record.emp_code)}</td><td>${dateLabel(record.date)}</td><td>${badge(record.status)}${record.late_minutes > 0 ? ` <span class="status-badge badge-late">${record.late_minutes}m late</span>` : ''}</td><td>${timeLabel(record.punch_in)}</td><td>${record.work_hours == null ? '—' : `${Number(record.work_hours).toFixed(2)} h`}</td></tr>`).join('') : `<tr><td colspan="5" class="empty-state">No attendance records yet. Record a punch-in to see the day take shape.</td></tr>`;
}
function renderAttendance() {
  const query = ($('#attendance-search')?.value || '').trim().toLowerCase();
  const day = $('#attendance-date')?.value || '';
  const status = $('#attendance-status')?.value || '';
  const filtered = state.attendance.filter(record => (!query || `${record.emp_code} ${employeeByCode(record.emp_code)?.name || ''}`.toLowerCase().includes(query)) && (!day || record.date === day) && (!status || record.status === status));
  $('#attendance-table').innerHTML = filtered.length ? filtered.map(record => `<tr><td>${employeeIdentity(record.emp_code)}</td><td>${dateLabel(record.date)}</td><td>${badge(record.status)}${record.late_minutes > 0 ? ` <span class="status-badge badge-late">${record.late_minutes}m</span>` : ''}</td><td>${timeLabel(record.punch_in)}</td><td>${timeLabel(record.punch_out)}</td><td>${record.work_hours == null ? '—' : `${Number(record.work_hours).toFixed(2)} h`}</td><td>${record.late_minutes > 0 ? `${record.late_minutes} min` : '—'}</td><td><button class="table-action" data-regularize="${safe(record.emp_code)}" data-date="${safe(record.date)}">Correct</button></td></tr>`).join('') : `<tr><td colspan="8" class="empty-state">No records match these filters.</td></tr>`;
  $('#attendance-footer').textContent = `${filtered.length} record${filtered.length === 1 ? '' : 's'} shown`;
}
function setPage(name) {
  const page = ['overview', 'employees', 'attendance', 'analytics'].includes(name) ? name : 'overview';
  $$('.page-view').forEach(view => view.classList.toggle('active', view.id === `page-${page}`));
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.page === page));
  $('#breadcrumb-current').textContent = page === 'analytics' ? 'Analytics' : page[0].toUpperCase() + page.slice(1);
  if (page === 'employees') renderEmployees();
  if (page === 'attendance') renderAttendance();
  if (page === 'analytics') populateEmployeeControls();
}
function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  if (id === 'employee-modal') {
    const dateInput = modal.querySelector('[name="joined_on"]');
    if (!dateInput.value) dateInput.value = todayIST();
  }
  if (id.includes('punch-')) {
    const timeInput = modal.querySelector('[name="punched_at"]');
    timeInput.value = '';
    modal.querySelector('[data-date-input]').value = '';
    modal.querySelector('[data-time-input]').value = '';
    modal.querySelector('[data-time-status]').textContent = 'Leave blank to use the current time.';
  }
  if (id === 'punch-in-modal') syncPunchInMode();
  modal.showModal();
}
function syncPunchInMode() {
  const form = $('#punch-in-form');
  if (!form) return;
  const nonWorking = ['ABSENT', 'LEAVE'].includes(form.elements.status.value);
  const control = form.querySelector('.datetime-control');
  const timeInput = form.querySelector('[data-time-input]');
  const confirmButton = form.querySelector('[data-confirm-time]');
  control.classList.toggle('is-non-working', nonWorking);
  timeInput.hidden = nonWorking;
  confirmButton.hidden = nonWorking;
  form.querySelector('[data-time-label]').textContent = nonWorking ? 'Attendance date' : 'Punch-in time';
  form.querySelector('[data-punch-hint]').textContent = nonWorking
    ? 'Absence and leave records do not have punch times.'
    : 'Choose both a date and time, then press OK to confirm.';
  form.querySelector('[data-time-status]').textContent = nonWorking
    ? 'No punch times will be recorded.'
    : 'Leave blank to use the current time.';
  if (nonWorking) form.elements.punched_at.value = '';
}
function openRegularize(code, day) {
  const record = state.attendance.find(item => item.emp_code === code && item.date === day);
  if (!record) return;
  const form = $('#regularize-form');
  form.elements.emp_code.value = code;
  form.elements.date.value = day;
  form.elements.new_date.value = day;
  form.elements.new_date.max = todayIST();
  form.elements.status.value = record.status;
  form.elements.punch_in.value = inputDateTime(record.punch_in);
  form.elements.punch_out.value = inputDateTime(record.punch_out);
  form.elements.clear_punch_out.checked = false;
  form.elements.reason.value = '';
  form.elements.regularized_by.value = state.profile?.name || state.profile?.email || '';
  $('#regularize-modal').showModal();
}
function closeModal(button) { button.closest('dialog')?.close(); }
function formObject(form) { return Object.fromEntries(new FormData(form).entries()); }

function openEmployeeDelete(code) {
  const employee = employeeByCode(code);
  if (!employee) return;
  const dialog = $('#delete-employee-modal');
  dialog.dataset.empCode = employee.emp_code;
  $('#delete-employee-name').textContent = employee.name;
  $('#delete-employee-code').textContent = employee.emp_code;
  $('#delete-employee-code-hint').textContent = employee.emp_code;
  $('#delete-employee-confirmation').value = '';
  $('#confirm-employee-delete').disabled = true;
  dialog.showModal();
  $('#delete-employee-confirmation').focus();
}

async function submitEmployeeDelete(event) {
  event.preventDefault();
  const dialog = event.currentTarget.closest('dialog');
  const code = dialog.dataset.empCode;
  const confirmation = $('#delete-employee-confirmation').value;
  if (confirmation !== code) return;
  const button = $('#confirm-employee-delete');
  button.disabled = true;
  button.textContent = 'Deleting…';
  try {
    const result = await request(`/employees/${encodeURIComponent(code)}`, { method: 'DELETE', body: JSON.stringify({ confirmation }) });
    dialog.close();
    event.currentTarget.reset();
    await refreshAll();
    notify(`Deleted ${code}, ${result.attendance_records_deleted} attendance record(s), and ${result.account_deleted ? 'its sign-in account' : 'no sign-in account'}.`);
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = $('#delete-employee-confirmation').value !== code;
    button.textContent = 'Delete employee';
  }
}

async function submitEmployee(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = formObject(form);
  const accountPassword = body.account_password;
  delete body.account_password;
  try {
    await request('/employees', { method: 'POST', body: JSON.stringify(body) });
    try {
      await request('/admin/employee-accounts', { method: 'POST', body: JSON.stringify({ emp_code: body.emp_code, password: accountPassword }) });
    } catch (accountError) {
      form.closest('dialog').close(); form.reset(); await loadEmployees();
      notify(`Employee added, but sign-in setup failed: ${accountError.message}`, true);
      return;
    }
    form.closest('dialog').close(); form.reset();
    await loadEmployees();
    notify(`${body.name} was added to the team.`);
  } catch (error) { notify(error.message, true); }
}

async function loadEmployeeHome() {
  const month = todayIST().slice(0, 7);
  const [summary, history] = await Promise.all([request(`/me/attendance/monthly?month=${month}`), request('/me/attendance?page=1&page_size=100')]);
  $('#my-present-days').textContent = summary.present_days;
  $('#my-attendance-pct').textContent = summary.attendance_pct == null ? '—' : `${summary.attendance_pct}%`;
  $('#my-late-count').textContent = summary.late_count;
  $('#my-overtime').textContent = `${Math.floor(summary.total_overtime_minutes / 60)}h ${summary.total_overtime_minutes % 60}m`;
  $('#my-history').innerHTML = history.items.length ? history.items.map(item => `<tr><td>${dateLabel(item.date)}</td><td>${badge(item.status)}</td><td>${timeLabel(item.punch_in)}</td><td>${timeLabel(item.punch_out)}</td><td>${item.work_hours == null ? '—' : `${Number(item.work_hours).toFixed(2)} h`}</td></tr>`).join('') : '<tr><td colspan="5" class="empty-state">No attendance records yet.</td></tr>';
}
function showProfile(profile) {
  state.profile = profile;
  $('#login-screen').hidden = true;
  $('#admin-app').hidden = profile.role !== 'admin';
  $('#employee-app').hidden = profile.role !== 'employee';
  if (profile.role === 'admin') {
    $('#profile-name').textContent = profile.name;
    $('#profile-initials').textContent = initials(profile.name);
    $('#top-initials').textContent = initials(profile.name);
    refreshAll();
  } else {
    $('#employee-name').textContent = profile.name;
    $('#employee-department').textContent = `${profile.department} · ${profile.emp_code} · Shift ${profile.shift_start}–${profile.shift_end} IST`;
    loadEmployeeHome().catch(error => notify(error.message, true));
  }
}
async function signOut() {
  try { await request('/auth/logout', { method: 'POST' }); } catch {}
  state.profile = null;
  $('#admin-app').hidden = true; $('#employee-app').hidden = true; $('#login-screen').hidden = false;
  $('#login-form').reset();
}
async function submitPunch(event, direction) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = formObject(form);
  const nonWorking = direction === 'in' && ['ABSENT', 'LEAVE'].includes(body.status);
  if (nonWorking) {
    body.date = form.querySelector('[data-date-input]').value || todayIST();
    delete body.punched_at;
  }
  if (!nonWorking && (form.querySelector('[data-date-input]').value || form.querySelector('[data-time-input]').value) && !body.punched_at) {
    notify('Press OK to confirm the selected date and time first.', true);
    return;
  }
  if (body.punched_at) body.punched_at = msFromInput(body.punched_at);
  else delete body.punched_at;
  const endpoint = nonWorking ? '/attendance/non-working' : direction === 'in' ? '/attendance/punch-in' : '/attendance/punch-out';
  try {
    await request(endpoint, { method: 'POST', body: JSON.stringify(body) });
    form.closest('dialog').close(); form.reset();
    await loadAttendance();
    notify(nonWorking ? `${humanStatus(body.status)} attendance recorded.` : direction === 'in' ? 'Punch-in recorded.' : 'Punch-out recorded.');
  } catch (error) { notify(error.message, true); }
}
async function submitRegularization(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const values = formObject(form);
  const body = { new_date: values.new_date, status: values.status, reason: values.reason, regularized_by: values.regularized_by };
  if (form.elements.clear_punch_out.checked) body.clear_punch_out = true;
  if (!['ABSENT', 'LEAVE'].includes(values.status)) {
    if (values.punch_in) body.punch_in = msFromInput(values.punch_in);
    if (values.punch_out) body.punch_out = msFromInput(values.punch_out);
  }
  try {
    await request(`/attendance/${encodeURIComponent(values.emp_code)}/${encodeURIComponent(values.date)}`, { method: 'PATCH', body: JSON.stringify(body) });
    $('#regularize-modal').close();
    await loadAttendance();
    notify('Attendance correction saved to history.');
  } catch (error) { notify(error.message, true); }
}
async function runAnalytics() {
  const month = $('#analytics-month').value;
  const department = $('#analytics-department').value;
  if (!month) { notify('Choose a month first.', true); return; }
  const summaryUrl = `/analytics/departments/summary?month=${encodeURIComponent(month)}${department ? `&department=${encodeURIComponent(department)}` : ''}`;
  const boardUrl = `/analytics/leaderboard/late?month=${encodeURIComponent(month)}&limit=5${department ? `&department=${encodeURIComponent(department)}` : ''}`;
  const container = $('#analytics-results');
  container.innerHTML = `<section class="panel analytics-placeholder"><span class="placeholder-icon">◌</span><h2>Putting the picture together…</h2><p>Gathering your attendance data.</p></section>`;
  try {
    const trendPromise = department ? request(`/analytics/departments/${encodeURIComponent(department)}/trend?from=${month}-01&to=${month}-${new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0).getDate()}`) : Promise.resolve(null);
    const [summary, leaderboard, trend] = await Promise.all([request(summaryUrl), request(boardUrl), trendPromise]);
    const items = summary.items || [];
    const headcount = items.reduce((total, item) => total + item.headcount, 0);
    const presentDays = items.reduce((total, item) => total + item.present_days, 0);
    const lateMinutes = items.reduce((total, item) => total + item.total_late_minutes, 0);
    const rows = items.map(item => `<tr><td><b>${safe(item.department)}</b></td><td>${item.headcount}</td><td>${Number(item.present_days).toFixed(1)}</td><td>${item.avg_work_hours == null ? '—' : `${Number(item.avg_work_hours).toFixed(2)} h`}</td><td>${item.late_count}</td><td>${item.leave_count}</td></tr>`).join('');
    const leaders = (leaderboard.items || []).map(item => `<tr><td><span class="rank-chip">${item.rank}</span></td><td>${employeeIdentity(item.emp_code)}</td><td>${safe(item.department)}</td><td><b>${item.total_late_minutes} min</b></td><td>${item.late_count}</td></tr>`).join('');
    const trendSection = trend ? `<section class="panel analytics-table trend-panel"><div class="panel-heading"><div><div class="section-kicker">DAILY RHYTHM</div><h2>${safe(department)} · Attendance trend</h2></div><span class="muted-text">Daily rate · 7-day moving average in API</span></div><div class="trend-bars">${trend.items.map(day => { const rate = day.attendance_rate == null ? null : Math.max(0, Math.min(1, day.attendance_rate)); const height = rate == null ? 3 : Math.max(4, rate * 72); return `<div class="trend-day" title="${safe(day.date)} · ${rate == null ? 'No rate' : `${(rate * 100).toFixed(2)}%`} · ${day.present_count}/${day.headcount} present"><div class="trend-bar ${rate == null ? 'no-rate' : ''}" style="height:${height}%"></div><small>${Number(day.date.slice(-2))}</small></div>`; }).join('')}</div><div class="trend-legend"><span><i></i> Daily attendance rate</span><span>Hover bars for daily detail</span></div></section>` : `<section class="panel trend-hint"><span class="placeholder-icon">⌁</span><span>Select a department above to see its daily trend.</span></section>`;
    container.innerHTML = `<div class="analytics-cards"><article class="panel analytic-metric"><span>Employees in scope</span><b>${headcount}</b></article><article class="panel analytic-metric"><span>Present days this month</span><b>${Number(presentDays).toFixed(1)}</b></article><article class="panel analytic-metric"><span>Total late minutes</span><b>${lateMinutes.toLocaleString()}</b></article></div><section class="panel analytics-table"><div class="panel-heading"><div><div class="section-kicker">MONTHLY BREAKDOWN</div><h2>${safe(month)} · Department summary</h2></div></div><div class="table-wrap"><table><thead><tr><th>DEPARTMENT</th><th>HEADCOUNT</th><th>PRESENT DAYS</th><th>AVG HOURS</th><th>LATE DAYS</th><th>LEAVE DAYS</th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="empty-state">No department data for this month.</td></tr>'}</tbody></table></div></section>${trendSection}<section class="panel analytics-table"><div class="panel-heading"><div><div class="section-kicker">ON THE CLOCK</div><h2>Late arrivals leaderboard</h2></div></div><div class="table-wrap"><table><thead><tr><th>RANK</th><th>EMPLOYEE</th><th>DEPARTMENT</th><th>LATE TIME</th><th>LATE DAYS</th></tr></thead><tbody>${leaders || '<tr><td colspan="5" class="empty-state">No late arrivals in this period.</td></tr>'}</tbody></table></div></section>`;
  } catch (error) {
    container.innerHTML = `<section class="panel analytics-placeholder"><span class="placeholder-icon">!</span><h2>We couldn’t load this report.</h2><p>${safe(error.message)}</p></section>`;
  }
}

document.addEventListener('click', event => {
  const nav = event.target.closest('[data-page], [data-page-link]');
  if (nav) { event.preventDefault(); const page = nav.dataset.page || nav.dataset.pageLink; location.hash = page; setPage(page); }
  const open = event.target.closest('[data-open-modal]');
  if (open) openModal(open.dataset.openModal);
  const close = event.target.closest('[data-close-modal]');
  if (close) closeModal(close);
  const detail = event.target.closest('[data-employee]');
  if (detail) {
    const employee = employeeByCode(detail.dataset.employee);
    if (employee) notify(`${employee.name} · ${employee.email} · Joined ${employee.joined_on}`);
  }
  const deleteEmployee = event.target.closest('[data-delete-employee]');
  if (deleteEmployee) openEmployeeDelete(deleteEmployee.dataset.deleteEmployee);
  const correct = event.target.closest('[data-regularize]');
  if (correct) openRegularize(correct.dataset.regularize, correct.dataset.date);
  const confirmTime = event.target.closest('[data-confirm-time]');
  if (confirmTime) {
    const form = confirmTime.closest('form');
    const day = form.querySelector('[data-date-input]').value;
    const clock = form.querySelector('[data-time-input]').value;
    const hidden = form.elements.punched_at;
    const status = form.querySelector('[data-time-status]');
    if (!day && !clock) {
      hidden.value = '';
      status.textContent = 'Confirmed: use the current time.';
      return;
    }
    if (!day || !clock) {
      notify('Choose both a date and a time, then press OK.', true);
      return;
    }
    hidden.value = `${day}T${clock}`;
    status.textContent = `Confirmed: ${day} at ${clock}`;
  }
});
$('#employee-form').addEventListener('submit', submitEmployee);
$('#delete-employee-form').addEventListener('submit', submitEmployeeDelete);
$('#delete-employee-confirmation').addEventListener('input', event => {
  $('#confirm-employee-delete').disabled = event.currentTarget.value !== $('#delete-employee-modal').dataset.empCode;
});
$('#punch-in-form').addEventListener('submit', event => submitPunch(event, 'in'));
$('#punch-in-form').elements.status.addEventListener('change', syncPunchInMode);
$('#punch-out-form').addEventListener('submit', event => submitPunch(event, 'out'));
$('#regularize-form').addEventListener('submit', submitRegularization);
$('#regularize-form').elements.clear_punch_out.addEventListener('change', event => {
  if (event.currentTarget.checked) $('#regularize-form').elements.punch_out.value = '';
});
$('#refresh-button').addEventListener('click', refreshAll);
$('#logout-button').addEventListener('click', signOut);
$('#employee-logout').addEventListener('click', signOut);
$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  $('#login-error').textContent = '';
  try {
    await request('/auth/login', { method: 'POST', body: JSON.stringify(formObject(form)) });
    const profile = await request('/auth/me');
    showProfile(profile);
  } catch (error) { $('#login-error').textContent = error.message; }
});
$('#my-punch-in').addEventListener('click', async () => {
  try { await request('/me/attendance/punch-in', { method: 'POST', body: JSON.stringify({ emp_code: state.profile.emp_code, status: 'PRESENT' }) }); await loadEmployeeHome(); notify('Punch-in recorded.'); } catch (error) { notify(error.message, true); }
});
$('#my-punch-out').addEventListener('click', async () => {
  try { await request('/me/attendance/punch-out', { method: 'POST', body: JSON.stringify({ emp_code: state.profile.emp_code }) }); await loadEmployeeHome(); notify('Punch-out recorded.'); } catch (error) { notify(error.message, true); }
});
$('#employee-search').addEventListener('input', renderEmployees);
$('#department-filter').addEventListener('change', renderEmployees);
$('#attendance-search').addEventListener('input', renderAttendance);
$('#attendance-date').addEventListener('change', renderAttendance);
$('#attendance-status').addEventListener('change', renderAttendance);
$$('[data-date-input], [data-time-input]').forEach(input => input.addEventListener('change', () => {
  const form = input.closest('form');
  form.elements.punched_at.value = '';
  if (form.id === 'punch-in-form' && ['ABSENT', 'LEAVE'].includes(form.elements.status.value)) {
    form.querySelector('[data-time-status]').textContent = input.value ? `Selected attendance date: ${input.value}` : 'No punch times will be recorded.';
    return;
  }
  form.querySelector('[data-time-status]').textContent = 'Selection changed. Press OK to confirm.';
}));
$('#load-analytics').addEventListener('click', runAnalytics);
$('#analytics-month').value = todayIST().slice(0, 7);
$$('dialog').forEach(dialog => dialog.addEventListener('close', () => {
  const toast = $('#toast');
  if (toast.parentElement === dialog) document.body.append(toast);
}));
window.addEventListener('hashchange', () => setPage(location.hash.slice(1)));
setPage(location.hash.slice(1) || 'overview');
(async () => {
  try { showProfile(await request('/auth/me')); }
  catch { $('#login-screen').hidden = false; $('#admin-app').hidden = true; $('#employee-app').hidden = true; }
})();

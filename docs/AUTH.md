# Authentication, Roles & Admin Assignment

## JWT authentication

Enable auth in `.env`:

```env
AUTH_ENABLED=true
SECRET_KEY=<a-long-random-secret-at-least-32-characters>
API_KEYS=fallback-admin-key:admin,fallback-user-key:user
```

Log in to receive tokens:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin123!"}'
```

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user": { "username": "admin", "role": "admin", "department": "IT Administration" }
}
```

Send the access token as `Authorization: Bearer <access_token>`. Static API keys
(`X-API-Key` or Bearer) still work — both are accepted by every protected route.

### Seeded accounts

| Username | Password | Role | Department |
|---|---|---|---|
| `admin` | `Admin123!` | admin | IT Administration |
| `employee` | `User123!` | user | Engineering |
| `technician` | `Tech123!` | technician | IT Support |

> Change these passwords before exposing the app. Admins can create users via
> `POST /auth/users` and change roles via `PATCH /auth/users/{username}/role`.

## Roles

| Role | Allowed |
|---|---|
| `user` | Chat, KB ingest, create service requests, track own tickets |
| `technician` | user + technician work queue |
| `admin` | everything: approvals, assignment, priority control, audit, user management |

## Admin assigns tasks and priorities **according to the user**

```mermaid
sequenceDiagram
    participant E as Employee (JWT)
    participant S as System
    participant A as Admin (JWT)
    participant T as Technician

    E->>S: report issue (authenticated as "employee")
    S->>S: classify + apply per-user rules
    S->>A: pending approval queue (shows requester + department)
    A->>S: POST /admin/tickets/SR-00001/assign
    Note over A,S: priority=critical, technician=Liam, justification
    S->>T: assigned ticket with final priority
    T->>S: in_progress → resolved
```

### Admin endpoints

| Endpoint | Purpose |
|---|---|
| `GET /admin/tickets` | All tickets with requester identity, role, department and matching rules |
| `POST /admin/tickets/{id}/assign` | Approve, override priority, assign technician, set status |
| `GET /admin/rules` / `POST /admin/rules` | Per-user / per-department default priority and technician |
| `DELETE /admin/rules` | Clear all routing rules |

**Example — assign a task and raise priority for a specific user:**

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/tickets/SR-00001/assign \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"priority":"critical","technician":"Liam O'\''Connor","note":"Deadline today"}'
```

**Example — define a rule so every request from `employee` starts as `high`:**

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/rules \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"username":"employee","priority":"high","technician":"Sofia Ramirez"}'
```

AI proposes an initial priority; the admin rule can override it at creation and
the admin can override it again at assignment. Nothing is approved automatically.

## Frontend

- **Sign in** button in the sidebar; JWT stored in `localStorage` only.
- User chip shows current username and role.
- Service desk **Admin assignments** tab lists every ticket with requester
  identity, a priority dropdown, technician field and **Assign** button.

## Database

The default store is a local SQLite file (`data/enterprise.db`). To use
PostgreSQL (persistent business records as recommended):

```bash
pip install -r requirements-postgres.txt
# .env:
DATABASE_URL=postgresql://user:password@host:5432/enterprise
```

`app/database/connection.py` translates `?` placeholders to `%s` and provides
dict-like rows, so `itsm.py` and `users.py` work on both backends unchanged. If
`psycopg` is not installed, the app logs a warning and keeps using SQLite.

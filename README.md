# libre-stage user-management service

Leeres, eigenständiges Projekt für **Auth + Usermanagement** aus `../libre-stage`.

## Enthalten

- FastAPI-Backend mit Endpunkten für:
  - `POST /login`
  - `POST /refresh`
  - `POST /logout`
  - `GET /csrf`
  - `GET /me`
  - `PUT /update_user`
  - `PUT /change_password`
  - `GET /user_list`
  - `GET/POST/PUT/DELETE /admin/users...`
  - `PUT /admin/trigger_password_reset/{user_id}`
  - `GET /password_reset/verify_reset_token`
  - `POST /password_reset/new_password`
- Datenbank nur mit User-relevanten Tabellen:
  - `users`
  - `refresh_tokens`
  - `token_blacklist`
  - `used_password_reset_tokens`
- Alembic für Migrationen
- Taskfile
- Demo-User-Seeding (übernommen aus `backend/migrations/init_demo_db.py` von libre-stage):
  - `admin` (Passwort: `Admin1234!`)
  - `alice`, `bob`, `carol`, `dave` (Passwort: `Demo1234!`)
- Frontend (SvelteKit) mit ausschließlich Auth-/Usermanagement-Routen:
  - `/` (Login)
  - `/users` (Profil + Admin-Benutzerverwaltung)
  - `/password_reset`

## Schnellstart

1. `.env.example` nach `.env` kopieren und Werte prüfen.
2. `task install`
3. `task db:migrate`
4. `task db:seed-demo`
5. `task backend-dev`
6. In zweitem Terminal: `task frontend-dev`

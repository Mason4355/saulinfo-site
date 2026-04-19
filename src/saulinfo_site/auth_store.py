from contextlib import closing
from datetime import datetime, timedelta
import secrets
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

from saulinfo_site.config import Config


class AuthStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or Config.AUTH_DB_PATH

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    auth_user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    display_name TEXT,
                    google_sub TEXT UNIQUE,
                    linked_shop_user_id INTEGER,
                    support_last_seen_at TIMESTAMP,
                    last_login_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(auth_users)").fetchall()
            }
            if "display_name" not in columns:
                conn.execute("ALTER TABLE auth_users ADD COLUMN display_name TEXT")
            if "google_sub" not in columns:
                conn.execute("ALTER TABLE auth_users ADD COLUMN google_sub TEXT")
            if "support_last_seen_at" not in columns:
                conn.execute("ALTER TABLE auth_users ADD COLUMN support_last_seen_at TIMESTAMP")
            if "last_login_at" not in columns:
                conn.execute("ALTER TABLE auth_users ADD COLUMN last_login_at TIMESTAMP")
            conn.commit()

    def create_user(self, email: str, password: str, linked_shop_user_id: int | None = None) -> tuple[bool, str]:
        cleaned_email = (email or "").strip().lower()
        cleaned_password = (password or "").strip()
        if not cleaned_email or "@" not in cleaned_email:
            return False, "Укажите корректный e-mail."
        if len(cleaned_password) < 6:
            return False, "Пароль должен быть не короче 6 символов."

        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO auth_users (email, password_hash, display_name, linked_shop_user_id, last_login_at, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        cleaned_email,
                        generate_password_hash(cleaned_password),
                        None,
                        int(linked_shop_user_id) if linked_shop_user_id not in (None, "") else None,
                    ),
                )
                conn.commit()
            return True, "Аккаунт создан."
        except sqlite3.IntegrityError:
            return False, "Такой e-mail уже зарегистрирован."

    def authenticate(self, email: str, password: str) -> dict | None:
        cleaned_email = (email or "").strip().lower()
        if not cleaned_email or not password:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT auth_user_id, email, display_name, google_sub, password_hash, linked_shop_user_id,
                       support_last_seen_at, last_login_at, created_at, updated_at
                FROM auth_users
                WHERE email = ?
                LIMIT 1
                """,
                (cleaned_email,),
            ).fetchone()
            if not row:
                return None
            if not check_password_hash(row["password_hash"], password):
                return None
            conn.execute(
                """
                UPDATE auth_users
                SET last_login_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE auth_user_id = ?
                """,
                (int(row["auth_user_id"]),),
            )
            conn.commit()
            return self.get_user(int(row["auth_user_id"]))

    def get_user(self, auth_user_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT auth_user_id, email, display_name, google_sub, linked_shop_user_id,
                       support_last_seen_at, last_login_at, created_at, updated_at
                FROM auth_users
                WHERE auth_user_id = ?
                LIMIT 1
                """,
                (int(auth_user_id),),
            ).fetchone()
            return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict | None:
        cleaned_email = (email or "").strip().lower()
        if not cleaned_email:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT auth_user_id, email, display_name, google_sub, linked_shop_user_id,
                       support_last_seen_at, last_login_at, created_at, updated_at
                FROM auth_users
                WHERE email = ?
                LIMIT 1
                """,
                (cleaned_email,),
            ).fetchone()
            return dict(row) if row else None

    def get_user_by_google_sub(self, google_sub: str) -> dict | None:
        cleaned_google_sub = (google_sub or "").strip()
        if not cleaned_google_sub:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT auth_user_id, email, display_name, google_sub, linked_shop_user_id,
                       support_last_seen_at, last_login_at, created_at, updated_at
                FROM auth_users
                WHERE google_sub = ?
                LIMIT 1
                """,
                (cleaned_google_sub,),
            ).fetchone()
            return dict(row) if row else None

    def mark_login(self, auth_user_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE auth_users
                SET last_login_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE auth_user_id = ?
                """,
                (int(auth_user_id),),
            )
            conn.commit()

    def create_or_update_google_user(self, email: str, google_sub: str, display_name: str | None = None) -> dict | None:
        cleaned_email = (email or "").strip().lower()
        cleaned_google_sub = (google_sub or "").strip()
        cleaned_name = (display_name or "").strip() or None
        if not cleaned_email or "@" not in cleaned_email or not cleaned_google_sub:
            return None

        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT auth_user_id
                FROM auth_users
                WHERE google_sub = ? OR email = ?
                ORDER BY CASE WHEN google_sub = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (cleaned_google_sub, cleaned_email, cleaned_google_sub),
            ).fetchone()

            if row:
                auth_user_id = int(row["auth_user_id"])
                conn.execute(
                    """
                    UPDATE auth_users
                    SET email = ?,
                        display_name = COALESCE(?, display_name),
                        google_sub = ?,
                        last_login_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE auth_user_id = ?
                    """,
                    (cleaned_email, cleaned_name, cleaned_google_sub, auth_user_id),
                )
                conn.commit()
                return self.get_user(auth_user_id)

            generated_password_hash = generate_password_hash(secrets.token_urlsafe(32))
            cursor = conn.execute(
                """
                INSERT INTO auth_users (email, password_hash, display_name, google_sub, last_login_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (cleaned_email, generated_password_hash, cleaned_name, cleaned_google_sub),
            )
            conn.commit()
            return self.get_user(int(cursor.lastrowid))

    def update_profile(self, auth_user_id: int, display_name: str) -> tuple[bool, str]:
        cleaned_name = (display_name or "").strip()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE auth_users
                SET display_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE auth_user_id = ?
                """,
                (cleaned_name or None, int(auth_user_id)),
            )
            conn.commit()
        return True, "Профиль обновлён."

    def link_shop_user(self, auth_user_id: int, shop_user_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE auth_users
                SET linked_shop_user_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE auth_user_id = ?
                """,
                (int(shop_user_id), int(auth_user_id)),
            )
            conn.commit()

    def mark_support_seen(self, auth_user_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE auth_users
                SET support_last_seen_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE auth_user_id = ?
                """,
                (int(auth_user_id),),
            )
            conn.commit()

    def change_password(self, auth_user_id: int, current_password: str, new_password: str) -> tuple[bool, str]:
        cleaned_new_password = (new_password or "").strip()
        if len(cleaned_new_password) < 6:
            return False, "Пароль должен быть не короче 6 символов."

        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT password_hash
                FROM auth_users
                WHERE auth_user_id = ?
                LIMIT 1
                """,
                (int(auth_user_id),),
            ).fetchone()
            if not row:
                return False, "Аккаунт не найден."
            if not check_password_hash(row["password_hash"], current_password or ""):
                return False, "Текущий пароль указан неверно."

            conn.execute(
                """
                UPDATE auth_users
                SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
                WHERE auth_user_id = ?
                """,
                (generate_password_hash(cleaned_new_password), int(auth_user_id)),
            )
            conn.commit()
        return True, "Пароль обновлён."

    def get_cleanup_candidates(self, older_than_days: int) -> list[dict]:
        threshold = datetime.utcnow() - timedelta(days=max(int(older_than_days or 0), 1))
        threshold_iso = threshold.strftime("%Y-%m-%d %H:%M:%S")
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT auth_user_id, email, display_name, google_sub, linked_shop_user_id,
                       support_last_seen_at, last_login_at, created_at, updated_at
                FROM auth_users
                WHERE COALESCE(linked_shop_user_id, 0) <= 0
                  AND COALESCE(last_login_at, updated_at, created_at) < ?
                ORDER BY COALESCE(last_login_at, updated_at, created_at) ASC
                """,
                (threshold_iso,),
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_user(self, auth_user_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "DELETE FROM auth_users WHERE auth_user_id = ?",
                (int(auth_user_id),),
            )
            conn.commit()

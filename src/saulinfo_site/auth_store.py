from contextlib import closing
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
                    linked_shop_user_id INTEGER,
                    support_last_seen_at TIMESTAMP,
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
            if "support_last_seen_at" not in columns:
                conn.execute("ALTER TABLE auth_users ADD COLUMN support_last_seen_at TIMESTAMP")
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
                    INSERT INTO auth_users (email, password_hash, display_name, linked_shop_user_id, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                SELECT auth_user_id, email, display_name, password_hash, linked_shop_user_id, support_last_seen_at, created_at, updated_at
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
            return {
                "auth_user_id": int(row["auth_user_id"]),
                "email": row["email"],
                "display_name": row["display_name"],
                "linked_shop_user_id": row["linked_shop_user_id"],
                "support_last_seen_at": row["support_last_seen_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def get_user(self, auth_user_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT auth_user_id, email, display_name, linked_shop_user_id, support_last_seen_at, created_at, updated_at
                FROM auth_users
                WHERE auth_user_id = ?
                LIMIT 1
                """,
                (int(auth_user_id),),
            ).fetchone()
            return dict(row) if row else None

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

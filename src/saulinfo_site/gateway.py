import sqlite3
from contextlib import closing

from saulinfo_site.config import Config


class ShopUpdateGateway:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or Config.SHOP_UPDATE_DB_PATH

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows}

    def get_user(self, user_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ? LIMIT 1",
                (int(user_id),),
            ).fetchone()
            return dict(row) if row else None

    def user_exists(self, user_id: int) -> bool:
        return self.get_user(user_id) is not None

    def get_user_keys(self, user_id: int) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM vpn_keys WHERE user_id = ? ORDER BY created_date DESC",
                (int(user_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_user_tickets(self, user_id: int) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM support_tickets WHERE user_id = ? ORDER BY updated_at DESC",
                (int(user_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_referrals(self, user_id: int) -> list[dict]:
        with closing(self._connect()) as conn:
            user_columns = self._get_columns(conn, "users")
            name_expr = "display_name" if "display_name" in user_columns else "username AS display_name"
            rows = conn.execute(
                f"""
                SELECT telegram_id, username, {name_expr}, total_spent, registration_date
                FROM users
                WHERE referred_by = ?
                ORDER BY registration_date DESC
                """,
                (int(user_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_hosts_with_plans(self) -> list[dict]:
        with closing(self._connect()) as conn:
            hosts = [dict(row) for row in conn.execute("SELECT * FROM xui_hosts ORDER BY host_name").fetchall()]
            for host in hosts:
                plans = conn.execute(
                    "SELECT * FROM plans WHERE host_name = ? ORDER BY months, price",
                    (host["host_name"],),
                ).fetchall()
                host["plans"] = [dict(row) for row in plans]
            return hosts

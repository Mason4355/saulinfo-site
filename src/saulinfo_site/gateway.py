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

    def _pick_existing_columns(self, available: set[str], *requested: str) -> list[str]:
        return [column for column in requested if column in available]

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
            selected_columns = ["telegram_id"]
            if "username" in user_columns:
                selected_columns.append("username")
            else:
                selected_columns.append("NULL AS username")
            if "display_name" in user_columns:
                selected_columns.append("display_name")
            elif "username" in user_columns:
                selected_columns.append("username AS display_name")
            else:
                selected_columns.append("NULL AS display_name")

            for column in self._pick_existing_columns(
                user_columns,
                "total_spent",
                "registration_date",
                "created_at",
                "balance",
            ):
                selected_columns.append(column)

            if "referred_by" not in user_columns:
                return []

            order_by = (
                "registration_date DESC"
                if "registration_date" in user_columns
                else "created_at DESC"
                if "created_at" in user_columns
                else "telegram_id DESC"
            )
            rows = conn.execute(
                f"SELECT {', '.join(selected_columns)} FROM users WHERE referred_by = ? ORDER BY {order_by}",
                (int(user_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_hosts_with_plans(self) -> list[dict]:
        with closing(self._connect()) as conn:
            host_columns = self._get_columns(conn, "xui_hosts")
            if not host_columns:
                return []

            order_by = "host_name" if "host_name" in host_columns else sorted(host_columns)[0]
            hosts = [dict(row) for row in conn.execute(f"SELECT * FROM xui_hosts ORDER BY {order_by}").fetchall()]
            for host in hosts:
                if "host_name" not in host:
                    host["plans"] = []
                    continue

                plan_columns = self._get_columns(conn, "plans")
                if "host_name" not in plan_columns:
                    host["plans"] = []
                    continue

                order_parts = [column for column in ("months", "price") if column in plan_columns]
                order_by_plans = ", ".join(order_parts) if order_parts else "rowid"
                plans = conn.execute(
                    f"SELECT * FROM plans WHERE host_name = ? ORDER BY {order_by_plans}",
                    (host["host_name"],),
                ).fetchall()
                host["plans"] = [dict(row) for row in plans]
            return hosts

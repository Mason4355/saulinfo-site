import sqlite3
from contextlib import closing
from datetime import datetime
import json
import re
from urllib.parse import urlparse

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

    def _normalize_host_name(self, name: str | None) -> str:
        cleaned = (name or "").strip()
        for char in ("\u00A0", "\u200B", "\u200C", "\u200D", "\uFEFF"):
            cleaned = cleaned.replace(char, "")
        return cleaned

    def _site_shop_user_id(self, auth_user_id: int) -> int:
        return -1_000_000_000 - int(auth_user_id)

    def _site_username(self, auth_user_id: int, email: str, display_name: str | None) -> str:
        base = (display_name or "").strip() or (email or "").split("@", 1)[0]
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._-")
        slug = slug[:18] or "user"
        return f"site_{int(auth_user_id)}_{slug}"

    def get_site_customer_id(self, auth_user_id: int) -> int:
        return self._site_shop_user_id(auth_user_id)

    def ensure_site_customer_record(self, auth_user_id: int, email: str, display_name: str | None = None) -> dict | None:
        shop_user_id = self._site_shop_user_id(auth_user_id)
        username = self._site_username(auth_user_id, email, display_name)
        with closing(self._connect()) as conn:
            user_columns = self._get_columns(conn, "users")
            if not user_columns or "telegram_id" not in user_columns:
                return None

            now = datetime.now()
            payload: dict = {"telegram_id": shop_user_id}
            if "username" in user_columns:
                payload["username"] = username
            if "display_name" in user_columns:
                payload["display_name"] = (display_name or "").strip() or username
            if "email" in user_columns:
                payload["email"] = (email or "").strip().lower() or None
            if "registration_date" in user_columns:
                payload["registration_date"] = now
            if "created_at" in user_columns:
                payload["created_at"] = now
            if "updated_at" in user_columns:
                payload["updated_at"] = now
            for column in ("total_spent", "balance", "referral_balance", "referral_balance_all"):
                if column in user_columns:
                    payload[column] = 0
            for column in ("total_months", "trial_used", "agreed_to_terms", "is_banned", "referral_start_bonus_received"):
                if column in user_columns:
                    payload[column] = 0
            if "referred_by" in user_columns:
                payload["referred_by"] = None

            existing = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ? LIMIT 1",
                (shop_user_id,),
            ).fetchone()
            if existing:
                updates = dict(payload)
                updates.pop("telegram_id", None)
                assignments = [f"{column} = ?" for column in updates]
                if assignments:
                    conn.execute(
                        f"UPDATE users SET {', '.join(assignments)} WHERE telegram_id = ?",
                        (*updates.values(), shop_user_id),
                    )
                conn.commit()
            else:
                columns = list(payload.keys())
                placeholders = ", ".join("?" for _ in columns)
                try:
                    conn.execute(
                        f"INSERT INTO users ({', '.join(columns)}) VALUES ({placeholders})",
                        tuple(payload[column] for column in columns),
                    )
                    conn.commit()
                except sqlite3.IntegrityError:
                    fallback_payload = dict(payload)
                    if "username" in fallback_payload:
                        fallback_payload["username"] = f"site_{int(auth_user_id)}"
                    # Some shop-update deployments may keep email unique in users.
                    # Site-only support does not require duplicating email there.
                    fallback_payload.pop("email", None)
                    columns = list(fallback_payload.keys())
                    placeholders = ", ".join("?" for _ in columns)
                    conn.execute(
                        f"INSERT INTO users ({', '.join(columns)}) VALUES ({placeholders})",
                        tuple(fallback_payload[column] for column in columns),
                    )
                    conn.commit()

            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ? LIMIT 1",
                (shop_user_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_setting(self, key: str) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ? LIMIT 1",
                ((key or "").strip(),),
            ).fetchone()
            if not row:
                return None
            return str(row[0]) if row[0] is not None else None

    def get_enabled_site_payment_methods(self) -> list[dict]:
        methods: list[dict] = [
            {
                "code": "balance",
                "label": "Баланс аккаунта",
                "provider": "SaulInfo",
                "note": "Списание с внутреннего баланса клиента.",
            }
        ]

        yookassa_shop_id = (self.get_setting("yookassa_shop_id") or "").strip()
        yookassa_secret_key = (self.get_setting("yookassa_secret_key") or "").strip()
        if yookassa_shop_id and yookassa_secret_key:
            methods.append(
                {
                    "code": "yookassa",
                    "label": "Банковская карта / СБП",
                    "provider": "YooKassa",
                    "note": "Оплата через форму YooKassa с возвратом на сайт.",
                }
            )

        yoomoney_enabled = str(self.get_setting("yoomoney_enabled") or "").strip().lower() in {"1", "true", "yes", "on"}
        yoomoney_wallet = (self.get_setting("yoomoney_wallet") or "").strip()
        yoomoney_api_token = (self.get_setting("yoomoney_api_token") or "").strip()
        if yoomoney_enabled and yoomoney_wallet and yoomoney_api_token:
            methods.append(
                {
                    "code": "yoomoney",
                    "label": "ЮMoney",
                    "provider": "YooMoney",
                    "note": "Быстрый платёж ЮMoney с проверкой возврата на сайт.",
                }
            )

        return methods

    def get_host(self, host_name: str) -> dict | None:
        cleaned_host = self._normalize_host_name(host_name)
        if not cleaned_host:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM xui_hosts WHERE TRIM(host_name) = TRIM(?) LIMIT 1",
                (cleaned_host,),
            ).fetchone()
            return dict(row) if row else None

    def ensure_site_customer(self, auth_user_id: int, email: str, display_name: str | None = None) -> dict | None:
        shop_user_id = self._site_shop_user_id(auth_user_id)
        username = self._site_username(auth_user_id, email, display_name)
        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ? LIMIT 1",
                (shop_user_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE users SET username = ? WHERE telegram_id = ?",
                    (username, shop_user_id),
                )
                conn.commit()
                return dict(conn.execute("SELECT * FROM users WHERE telegram_id = ?", (shop_user_id,)).fetchone())

            conn.execute(
                """
                INSERT INTO users (telegram_id, username, registration_date)
                VALUES (?, ?, ?)
                """,
                (shop_user_id, username, datetime.now()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (shop_user_id,)).fetchone()
            return dict(row) if row else None

    def create_pending_transaction(self, payment_id: str, user_id: int, amount_rub: float, metadata: dict) -> int | None:
        payload = dict(metadata or {})
        payload.setdefault("payment_id", payment_id)
        with closing(self._connect()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO transactions (payment_id, user_id, status, amount_rub, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (payment_id or "").strip(),
                    int(user_id),
                    "pending",
                    float(amount_rub or 0),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def get_transaction_by_payment_id(self, payment_id: str) -> dict | None:
        cleaned_id = (payment_id or "").strip()
        if not cleaned_id:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM transactions WHERE payment_id = ? LIMIT 1",
                (cleaned_id,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            raw_metadata = item.get("metadata")
            try:
                item["parsed_metadata"] = json.loads(raw_metadata) if raw_metadata else {}
            except Exception:
                item["parsed_metadata"] = {}
            if not isinstance(item["parsed_metadata"], dict):
                item["parsed_metadata"] = {}
            return item

    def update_transaction_metadata(self, payment_id: str, metadata: dict) -> bool:
        cleaned_id = (payment_id or "").strip()
        if not cleaned_id:
            return False
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE transactions SET metadata = ? WHERE payment_id = ?",
                (json.dumps(metadata or {}, ensure_ascii=False), cleaned_id),
            )
            conn.commit()
            return True

    def finalize_pending_transaction(
        self,
        payment_id: str,
        payment_method: str,
        amount_rub: float | None = None,
        amount_currency: float | None = None,
        currency_name: str | None = None,
    ) -> dict | None:
        cleaned_id = (payment_id or "").strip()
        if not cleaned_id:
            return None
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM transactions WHERE payment_id = ? AND status = 'pending' LIMIT 1",
                (cleaned_id,),
            ).fetchone()
            if not row:
                return None

            conn.execute(
                """
                UPDATE transactions
                SET status = 'paid',
                    amount_rub = COALESCE(?, amount_rub),
                    amount_currency = COALESCE(?, amount_currency),
                    currency_name = COALESCE(?, currency_name),
                    payment_method = COALESCE(?, payment_method)
                WHERE payment_id = ?
                """,
                (amount_rub, amount_currency, currency_name, payment_method, cleaned_id),
            )
            conn.commit()

            try:
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            except Exception:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.setdefault("payment_id", cleaned_id)
            metadata["payment_method"] = payment_method
            return metadata

    def _build_subscription_link(self, host_name: str | None, client_uuid: str | None) -> str | None:
        cleaned_uuid = (client_uuid or "").strip()
        if not cleaned_uuid:
            return None

        host = self.get_host(host_name or "")
        host_url = ((host or {}).get("host_url") or "").strip()
        parsed_host = urlparse(host_url if "://" in host_url else f"https://{host_url}")
        raw_domain = (self.get_setting("domain") or "").strip()
        parsed_domain = urlparse(raw_domain if "://" in raw_domain else f"https://{raw_domain}") if raw_domain else None
        hostname = (parsed_domain.hostname if parsed_domain else "") or parsed_host.hostname or ""
        if not hostname:
            return None

        scheme = parsed_host.scheme if parsed_host.scheme in {"http", "https"} else "https"
        return f"{scheme}://{hostname}/sub/{cleaned_uuid}?format=v2ray"

    def get_user_keys(self, user_id: int) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM vpn_keys WHERE user_id = ? ORDER BY created_date DESC",
                (int(user_id),),
            ).fetchall()
            items: list[dict] = []
            for row in rows:
                item = dict(row)
                item["subscription_url"] = self._build_subscription_link(
                    item.get("host_name"),
                    item.get("xui_client_uuid"),
                )
                items.append(item)
            return items

    def get_balance(self, user_id: int) -> float:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT balance FROM users WHERE telegram_id = ? LIMIT 1",
                (int(user_id),),
            ).fetchone()
            if not row:
                return 0.0
            return float(row[0] or 0.0)

    def deduct_from_balance(self, user_id: int, amount: float) -> bool:
        normalized_amount = float(amount or 0)
        if normalized_amount <= 0:
            return False
        with closing(self._connect()) as conn:
            current_balance = self.get_balance(int(user_id))
            if current_balance < normalized_amount:
                return False
            conn.execute(
                "UPDATE users SET balance = balance - ? WHERE telegram_id = ?",
                (normalized_amount, int(user_id)),
            )
            conn.commit()
            return True

    def add_to_balance(self, user_id: int, amount: float) -> bool:
        normalized_amount = float(amount or 0)
        if normalized_amount <= 0:
            return False
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE telegram_id = ?",
                (normalized_amount, int(user_id)),
            )
            conn.commit()
            return True

    def get_plan_by_id(self, plan_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM plans WHERE plan_id = ? LIMIT 1",
                (int(plan_id),),
            ).fetchone()
            return dict(row) if row else None

    def get_key_by_id(self, key_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM vpn_keys WHERE key_id = ? LIMIT 1",
                (int(key_id),),
            ).fetchone()
            return dict(row) if row else None

    def get_key_by_email(self, key_email: str) -> dict | None:
        cleaned_email = (key_email or "").strip()
        if not cleaned_email:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM vpn_keys WHERE key_email = ? LIMIT 1",
                (cleaned_email,),
            ).fetchone()
            return dict(row) if row else None

    def generate_site_key_email(self, account_email: str, user_id: int) -> str:
        local = (account_email or "").split("@", 1)[0]
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", local).strip("._-")[:24] or f"user{int(user_id)}"
        suffix = 1
        while True:
            candidate = f"{slug}{'' if suffix == 1 else f'-{suffix}'}@site.local"
            if not self.get_key_by_email(candidate):
                return candidate
            suffix += 1

    def add_new_key(self, user_id: int, host_name: str, xui_client_uuid: str, key_email: str, expiry_timestamp_ms: int) -> int | None:
        normalized_host = self._normalize_host_name(host_name)
        expiry_ts = int(expiry_timestamp_ms or 0)
        expiry_date = datetime.fromtimestamp(expiry_ts / 1000) if expiry_ts > 0 else None
        with closing(self._connect()) as conn:
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO vpn_keys (user_id, host_name, xui_client_uuid, key_email, expiry_date)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (int(user_id), normalized_host, xui_client_uuid, key_email, expiry_date),
                )
                conn.commit()
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                existing = None
                if key_email:
                    existing = conn.execute(
                        "SELECT key_id FROM vpn_keys WHERE key_email = ? LIMIT 1",
                        (key_email,),
                    ).fetchone()
                if existing is None and xui_client_uuid:
                    existing = conn.execute(
                        "SELECT key_id FROM vpn_keys WHERE xui_client_uuid = ? LIMIT 1",
                        (xui_client_uuid,),
                    ).fetchone()
                if not existing:
                    return None
                key_id = int(existing[0])
                conn.execute(
                    """
                    UPDATE vpn_keys
                    SET user_id = ?, host_name = ?, xui_client_uuid = ?, key_email = ?, expiry_date = ?
                    WHERE key_id = ?
                    """,
                    (int(user_id), normalized_host, xui_client_uuid, key_email, expiry_date, key_id),
                )
                conn.commit()
                return key_id

    def update_key_info(self, key_id: int, new_xui_uuid: str, new_expiry_ms: int) -> bool:
        expiry_ts = int(new_expiry_ms or 0)
        expiry_date = datetime.fromtimestamp(expiry_ts / 1000) if expiry_ts > 0 else None
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE vpn_keys
                SET xui_client_uuid = ?, expiry_date = ?
                WHERE key_id = ?
                """,
                (new_xui_uuid, expiry_date, int(key_id)),
            )
            conn.commit()
            return True

    def update_user_stats(self, user_id: int, amount_spent: float, months_purchased: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE users SET total_spent = total_spent + ?, total_months = total_months + ? WHERE telegram_id = ?",
                (float(amount_spent or 0), int(months_purchased or 0), int(user_id)),
            )
            conn.commit()

    def log_balance_transaction(
        self,
        user_id: int,
        username: str,
        amount_rub: float,
        metadata: dict,
        payment_method: str = "Balance",
    ) -> None:
        payload = dict(metadata or {})
        payload.setdefault("payment_method", payment_method)
        payload.setdefault("created_via", "saulinfo-site")
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO transactions
                (username, payment_id, user_id, status, amount_rub, payment_method, metadata, created_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (username or "").strip() or f"site_{int(user_id)}",
                    f"site-balance-{int(user_id)}-{int(datetime.now().timestamp())}",
                    int(user_id),
                    "paid",
                    float(amount_rub or 0),
                    payment_method,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(),
                ),
            )
            conn.commit()

    def get_user_tickets(self, user_id: int) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM support_tickets WHERE user_id = ? ORDER BY updated_at DESC",
                (int(user_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_ticket_messages(self, ticket_id: int) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM support_messages WHERE ticket_id = ? ORDER BY created_at ASC",
                (int(ticket_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_support_ticket(self, user_id: int, subject: str | None, message: str) -> int | None:
        cleaned_message = (message or "").strip()
        cleaned_subject = (subject or "").strip() or "Обращение с сайта SaulInfo"
        if not cleaned_message:
            return None

        with closing(self._connect()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO support_tickets (user_id, subject) VALUES (?, ?)",
                (int(user_id), cleaned_subject),
            )
            ticket_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO support_messages (ticket_id, sender, content) VALUES (?, ?, ?)",
                (int(ticket_id), "user", cleaned_message),
            )
            cursor.execute(
                "UPDATE support_tickets SET updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
                (int(ticket_id),),
            )
            conn.commit()
            return int(ticket_id)

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

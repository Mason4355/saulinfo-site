import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

import requests

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for

from saulinfo_site.auth_store import AuthStore
from saulinfo_site.config import Config
from saulinfo_site.gateway import ShopUpdateGateway
from saulinfo_site.xui_bridge import create_or_update_key_on_host


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)

    Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    gateway = ShopUpdateGateway()
    auth_store = AuthStore()
    auth_store.initialize()
    cleanup_state = {"last_run": None}

    def safe_gateway_call(label: str, fn, fallback):
        try:
            return fn()
        except Exception:
            app.logger.exception("SaulInfo gateway call failed: %s", label)
            return fallback

    def ensure_customer_context(account: dict | None, user: dict | None) -> tuple[dict | None, dict | None]:
        if not account:
            return account, user

        if user:
            try:
                if account.get("linked_shop_user_id") != user.get("telegram_id"):
                    auth_store.link_shop_user(int(account["auth_user_id"]), int(user["telegram_id"]))
                    account = auth_store.get_user(int(account["auth_user_id"])) or account
            except Exception:
                app.logger.exception("Failed to sync linked shop user for auth user %s", account.get("auth_user_id"))
            return account, user

        try:
            fallback_shop_user_id = gateway.get_site_customer_id(int(account["auth_user_id"]))
        except Exception:
            fallback_shop_user_id = None

        if fallback_shop_user_id is not None:
            try:
                existing_user = gateway.get_user(int(fallback_shop_user_id))
            except Exception:
                app.logger.exception("Failed to load fallback site customer %s", fallback_shop_user_id)
                existing_user = None
            if existing_user:
                session["shop_user_id"] = int(existing_user["telegram_id"])
                try:
                    auth_store.link_shop_user(int(account["auth_user_id"]), int(existing_user["telegram_id"]))
                    account = auth_store.get_user(int(account["auth_user_id"])) or account
                except Exception:
                    app.logger.exception("Failed to persist existing site customer link for auth user %s", account.get("auth_user_id"))
                return account, existing_user

        try:
            ensured_user = gateway.ensure_site_customer_record(
                int(account["auth_user_id"]),
                account.get("email", ""),
                account.get("display_name"),
            )
        except Exception:
            app.logger.exception("Failed to ensure site customer for auth user %s", account.get("auth_user_id"))
            try:
                ensured_user = gateway.ensure_site_customer(
                    int(account["auth_user_id"]),
                    account.get("email", ""),
                    account.get("display_name"),
                )
            except Exception:
                app.logger.exception("Legacy site customer ensure also failed for auth user %s", account.get("auth_user_id"))
                ensured_user = None

        if not ensured_user:
            return account, None

        ensured_id = int(ensured_user["telegram_id"])
        session["shop_user_id"] = ensured_id
        try:
            auth_store.link_shop_user(int(account["auth_user_id"]), ensured_id)
            account = auth_store.get_user(int(account["auth_user_id"])) or account
        except Exception:
            app.logger.exception("Failed to persist linked shop user for auth user %s", account.get("auth_user_id"))
        return account, ensured_user

    def load_session_context() -> tuple[dict | None, dict | None]:
        current_user = None
        current_account = None
        auth_user_id = session.get("auth_user_id")
        shop_user_id = session.get("shop_user_id")

        if auth_user_id not in (None, -1):
            try:
                current_account = auth_store.get_user(int(auth_user_id))
            except Exception:
                app.logger.exception("Failed to load auth user %s", auth_user_id)
                current_account = None

            if current_account and current_account.get("linked_shop_user_id") is not None:
                shop_user_id = int(current_account["linked_shop_user_id"])

        if shop_user_id is not None:
            try:
                current_user = gateway.get_user(int(shop_user_id))
            except Exception:
                app.logger.exception("Failed to load shop user %s", shop_user_id)
                current_user = None

        if current_account:
            current_account, current_user = ensure_customer_context(current_account, current_user)

        return current_account, current_user

    def ensure_portal_customer(account: dict | None, user: dict | None) -> tuple[dict | None, dict | None]:
        account, user = ensure_customer_context(account, user)
        if not account or user:
            return account, user

        try:
            forced_user = gateway.ensure_site_customer_record(
                int(account["auth_user_id"]),
                account.get("email", ""),
                account.get("display_name"),
            )
        except Exception:
            app.logger.exception("Forced site customer ensure failed for auth user %s", account.get("auth_user_id"))
            try:
                forced_user = gateway.ensure_site_customer(
                    int(account["auth_user_id"]),
                    account.get("email", ""),
                    account.get("display_name"),
                )
            except Exception:
                app.logger.exception("Legacy forced site customer ensure failed for auth user %s", account.get("auth_user_id"))
                forced_user = None

        if not forced_user:
            return account, user

        session["shop_user_id"] = int(forced_user["telegram_id"])
        try:
            auth_store.link_shop_user(int(account["auth_user_id"]), int(forced_user["telegram_id"]))
            account = auth_store.get_user(int(account["auth_user_id"])) or account
        except Exception:
            app.logger.exception("Failed to persist forced site customer link for auth user %s", account.get("auth_user_id"))
        return account, forced_user

    def public_base_url() -> str:
        forwarded_host = (request.headers.get("X-Forwarded-Host") or "").strip()
        forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").strip()
        host = forwarded_host or request.host
        scheme = forwarded_proto or request.scheme or "https"
        return f"{scheme}://{host}".rstrip("/")

    def google_auth_enabled() -> bool:
        return bool(Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET)

    def google_redirect_uri() -> str:
        return f"{public_base_url()}{url_for('google_callback')}"

    def set_authenticated_session(account: dict) -> tuple[dict | None, dict | None]:
        session["auth_user_id"] = int(account["auth_user_id"])
        linked_shop_user_id = account.get("linked_shop_user_id")
        if linked_shop_user_id is not None:
            session["shop_user_id"] = int(linked_shop_user_id)
        else:
            session.pop("shop_user_id", None)

        auth_store.mark_login(int(account["auth_user_id"]))
        refreshed_account = auth_store.get_user(int(account["auth_user_id"])) or account
        refreshed_account, user = ensure_customer_context(refreshed_account, None)
        if user:
            session["shop_user_id"] = int(user["telegram_id"])
        return refreshed_account, user

    def run_inactive_account_cleanup() -> None:
        now = datetime.utcnow()
        last_run = cleanup_state.get("last_run")
        if last_run and now - last_run < timedelta(hours=max(int(Config.SITE_CLEANUP_INTERVAL_HOURS or 0), 1)):
            return

        cleanup_state["last_run"] = now
        try:
            candidates = auth_store.get_cleanup_candidates(Config.SITE_ACCOUNT_RETENTION_DAYS)
        except Exception:
            app.logger.exception("Failed to load stale site accounts for cleanup")
            return

        for account in candidates:
            auth_user_id = int(account["auth_user_id"])
            linked_shop_user_id = account.get("linked_shop_user_id")
            site_user_id = None
            if linked_shop_user_id is not None:
                try:
                    site_user_id = int(linked_shop_user_id)
                except (TypeError, ValueError):
                    site_user_id = None
            if site_user_id is None or site_user_id > 0:
                site_user_id = gateway.get_site_customer_id(auth_user_id)

            try:
                if gateway.has_active_keys(int(site_user_id)):
                    continue
            except Exception:
                app.logger.exception("Failed to inspect keys for stale site account %s", auth_user_id)
                continue

            try:
                gateway.purge_site_customer_records(auth_user_id)
                auth_store.delete_user(auth_user_id)
            except Exception:
                app.logger.exception("Failed to purge stale site account %s", auth_user_id)

    def get_site_payment_methods() -> list[dict]:
        methods = safe_gateway_call("enabled_site_payment_methods", gateway.get_enabled_site_payment_methods, [])
        defaults = {
            "balance": {
                "label": "Баланс аккаунта",
                "provider": "SaulInfo",
                "note": "Списание с внутреннего баланса клиентского профиля.",
            },
            "yookassa": {
                "label": "Банковская карта / СБП",
                "provider": "YooKassa",
                "note": "Оплата через форму YooKassa с возвратом обратно на сайт.",
            },
            "heleket": {
                "label": "Crypto",
                "provider": "Heleket",
                "note": "Оплата криптовалютой через Heleket с подтверждением в общей панели.",
            },
            "yoomoney": {
                "label": "ЮMoney",
                "provider": "YooMoney",
                "note": "Быстрый платёж ЮMoney с возвратом обратно на сайт.",
            },
            "cryptobot": {
                "label": "CryptoBot",
                "provider": "Crypto Pay",
                "note": "Оплата в USDT через CryptoBot с последующей выдачей доступа на сайте.",
            },
            "tonconnect": {
                "label": "TON Connect",
                "provider": "TON",
                "note": "Оплата через TON Connect с подтверждением в общей панели SaulInfo.",
            },
            "paritypay": {
                "label": "Банковская карта / QR",
                "provider": "ParityPay",
                "note": "Оплата через ParityPay с подтверждением в общей панели SaulInfo.",
            },
        }
        normalized: list[dict] = []
        seen_codes: set[str] = set()
        for item in methods or []:
            code = str(item.get("code") or "").strip().lower()
            if not code:
                continue
            fallback = defaults.get(code, {})
            seen_codes.add(code)
            normalized.append(
                {
                    "code": code,
                    "label": str(fallback.get("label") or item.get("label") or code),
                    "provider": str(fallback.get("provider") or item.get("provider") or "SaulInfo"),
                    "note": str(fallback.get("note") or item.get("note") or ""),
                }
            )

        fallback_sources = {
            "heleket": (gateway.get_setting("heleket_merchant_id"), gateway.get_setting("heleket_api_key")),
            "cryptobot": (gateway.get_setting("cryptobot_token"),),
            "paritypay": (
                gateway.get_setting("paritypay_shop_id"),
                gateway.get_setting("paritypay_api_secret_key"),
                gateway.get_setting("paritypay_callback_secret_key"),
            ),
            "tonconnect": (gateway.get_setting("ton_wallet_address"), gateway.get_setting("tonapi_key")),
        }
        for code, values in fallback_sources.items():
            if code in seen_codes:
                continue
            if all((value or "").strip() for value in values):
                normalized.append({"code": code, **defaults[code]})

        return normalized or [{"code": "balance", **defaults["balance"]}]

    def parse_db_timestamp(value: object) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        normalized = raw.replace("T", " ")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def has_unread_support_messages(account: dict | None, ticket_threads: dict[int, list[dict]]) -> bool:
        last_seen_raw = str((account or {}).get("support_last_seen_at") or "").strip()
        last_seen_at = parse_db_timestamp(last_seen_raw)
        for messages in ticket_threads.values():
            for message in messages or []:
                sender = str(message.get("sender") or "").strip().lower()
                if sender == "user":
                    continue
                created_raw = str(message.get("created_at") or "").strip()
                created_at = parse_db_timestamp(created_raw)
                if not last_seen_raw:
                    return True
                if created_at and last_seen_at and created_at > last_seen_at:
                    return True
                if created_raw and last_seen_raw and created_raw > last_seen_raw:
                    return True
        return False

    def deserialize_support_media(raw_media: object) -> list[dict]:
        if not raw_media:
            return []
        try:
            payload = json.loads(raw_media) if isinstance(raw_media, str) else raw_media
        except (TypeError, json.JSONDecodeError):
            return []
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return []
        items = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            filename = Path(str(item.get("filename") or "")).name
            if not filename:
                continue
            items.append(
                {
                    "filename": filename,
                    "original_name": str(item.get("original_name") or filename),
                    "mime_type": str(item.get("mime_type") or "").strip(),
                    "size": int(item.get("size") or 0),
                }
            )
        return items

    def normalize_support_messages(ticket_id: int, messages: list[dict]) -> list[dict]:
        normalized = []
        for message in messages or []:
            current = dict(message)
            current["media_items"] = [
                {
                    **media,
                    "url": url_for("support_media_file", ticket_id=int(ticket_id), filename=media["filename"]),
                }
                for media in deserialize_support_media(current.get("media"))
            ]
            normalized.append(current)
        return normalized

    @app.before_request
    def run_periodic_maintenance():
        run_inactive_account_cleanup()

    def build_dashboard_payload(account: dict | None, user: dict | None) -> dict:
        user_id = int(user["telegram_id"]) if user else None
        keys = safe_gateway_call("user_keys", lambda: gateway.get_user_keys(user_id), []) if user_id is not None else []
        tickets = safe_gateway_call("user_tickets", lambda: gateway.get_user_tickets(user_id), []) if user_id is not None else []
        referrals = safe_gateway_call("referrals", lambda: gateway.get_referrals(user_id), []) if user_id is not None else []
        hosts = safe_gateway_call("hosts_with_plans", gateway.get_hosts_with_plans, [])
        ticket_threads = {
            int(ticket.get("ticket_id")): normalize_support_messages(
                int(ticket.get("ticket_id")),
                safe_gateway_call(
                f"ticket_messages_{ticket.get('ticket_id')}",
                lambda ticket_id=int(ticket.get("ticket_id")): gateway.get_ticket_messages(ticket_id),
                [],
                ),
            )
            for ticket in tickets
            if ticket.get("ticket_id") is not None
        }

        active_keys = sum(1 for key in keys if (key.get("expiry_date") or "").strip())
        open_tickets = sum(
            1
            for ticket in tickets
            if str(ticket.get("status") or "").strip().lower() not in {"closed", "resolved", "done"}
        )
        support_has_unread = has_unread_support_messages(account, ticket_threads)

        return {
            "account": account,
            "user": user,
            "keys": keys,
            "tickets": tickets,
            "ticket_threads": ticket_threads,
            "referrals": referrals,
            "hosts": hosts,
            "active_keys_count": active_keys,
            "open_tickets_count": open_tickets,
            "support_has_unread": support_has_unread,
        }

    def build_keys_context(account: dict | None, user: dict | None) -> dict:
        payload = build_dashboard_payload(account, user)
        payload["payment_methods"] = get_site_payment_methods()
        return payload

    def build_yoomoney_quickpay_url(
        wallet: str,
        amount: float,
        label: str,
        success_url: str | None = None,
        targets: str | None = None,
    ) -> str:
        params = {
            "receiver": wallet,
            "quickpay-form": "shop",
            "sum": f"{float(amount):.2f}",
            "label": label,
        }
        if success_url:
            params["successURL"] = success_url
        if targets:
            params["targets"] = targets
        return f"https://yoomoney.ru/quickpay/confirm.xml?{urlencode(params)}"

    def find_yoomoney_payment(label: str) -> dict | None:
        token = (gateway.get_setting("yoomoney_api_token") or "").strip()
        if not token:
            return None

        try:
            response = requests.post(
                "https://yoomoney.ru/api/operation-history",
                data={"label": label, "records": "5"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=20,
            )
        except Exception:
            app.logger.exception("Failed to query YooMoney operation history for %s", label)
            return None
        if response.status_code != 200:
            return None

        payload = response.json()
        for operation in payload.get("operations") or []:
            if str(operation.get("label")) != str(label):
                continue
            if str(operation.get("direction") or "").lower() != "in":
                continue
            if str(operation.get("status") or "").lower() != "success":
                continue
            try:
                amount = float(operation.get("amount"))
            except Exception:
                amount = None
            return {
                "operation_id": operation.get("operation_id"),
                "amount": amount,
                "currency_name": "RUB",
            }
        return None

    def get_usdt_rub_rate() -> float | None:
        try:
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub",
                timeout=20,
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            value = payload.get("tether", {}).get("rub")
            return float(value) if value else None
        except Exception:
            app.logger.exception("Failed to fetch USDT/RUB rate")
            return None

    def create_cryptobot_invoice(metadata: dict) -> dict:
        token = (gateway.get_setting("cryptobot_token") or "").strip()
        if not token:
            raise RuntimeError("CryptoBot не настроен в панели.")

        rate = get_usdt_rub_rate()
        if not rate or rate <= 0:
            raise RuntimeError("Не удалось получить курс USDT/RUB для CryptoBot.")

        amount_usdt = round(float(metadata["price"]) / float(rate), 2)
        payload = ":".join(
            [
                str(metadata["user_id"]),
                str(metadata["months"]),
                str(float(metadata["price"])),
                str(metadata["action"]),
                str(metadata.get("key_id") or ""),
                str(metadata["host_name"]),
                str(metadata["plan_id"]),
                str(metadata.get("customer_email") or ""),
                "CryptoBot",
            ]
        )

        response = requests.post(
            "https://pay.crypt.bot/api/createInvoice",
            headers={"Crypto-Pay-API-Token": token},
            json={
                "asset": "USDT",
                "amount": amount_usdt,
                "description": "SaulInfo VPN payment",
                "payload": payload,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload_json = response.json()
        if not payload_json.get("ok"):
            raise RuntimeError(f"CryptoBot API error: {payload_json}")
        result = payload_json.get("result") or {}
        pay_url = result.get("pay_url") or result.get("bot_invoice_url")
        if not pay_url:
            raise RuntimeError("CryptoBot не вернул ссылку на оплату.")
        return {"payment_url": str(pay_url)}

    def build_ton_payment_url(payment_id: str, amount_rub: float) -> str:
        wallet_address = (gateway.get_setting("ton_wallet_address") or "").strip()
        if not wallet_address:
            raise RuntimeError("TON Connect не настроен в панели.")

        try:
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=toncoin&vs_currencies=usd,rub",
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            ton_rub = payload.get("toncoin", {}).get("rub")
            ton_amount = float(amount_rub) / float(ton_rub)
        except Exception:
            app.logger.exception("Failed to fetch TON/RUB rate")
            raise RuntimeError("Не удалось получить курс TON для оплаты.")

        amount_nanoton = int(ton_amount * 1_000_000_000)
        return f"ton://transfer/{wallet_address}?{urlencode({'amount': str(amount_nanoton), 'text': payment_id})}"

    def create_heleket_payment(metadata: dict) -> dict:
        merchant_id = (gateway.get_setting("heleket_merchant_id") or "").strip()
        api_key = (gateway.get_setting("heleket_api_key") or "").strip()
        if not merchant_id or not api_key:
            raise RuntimeError("Heleket не настроен в панели.")

        callback_base = (gateway.get_setting("domain") or "").strip() or Config.SHOP_UPDATE_PANEL_URL
        callback_url = f"{callback_base.rstrip('/')}/heleket-webhook"

        payload_metadata = {
            "payment_id": str(metadata["payment_id"]),
            "user_id": int(metadata["user_id"]),
            "months": int(metadata["months"]),
            "price": float(metadata["price"]),
            "action": str(metadata["action"]),
            "key_id": metadata.get("key_id"),
            "host_name": str(metadata["host_name"]),
            "plan_id": int(metadata["plan_id"]),
            "customer_email": str(metadata.get("customer_email") or ""),
            "payment_method": "Crypto",
        }

        data = {
            "merchant_id": merchant_id,
            "order_id": str(uuid.uuid4()),
            "amount": float(metadata["price"]),
            "currency": "RUB",
            "description": json.dumps(payload_metadata, ensure_ascii=False, separators=(",", ":")),
            "callback_url": callback_url,
            "success_url": f"{public_base_url()}{url_for('keys_payment_pending_page', payment_id=metadata['payment_id'])}",
        }

        sorted_data = json.dumps(data, sort_keys=True, separators=(",", ":"))
        sign = hashlib.md5(f"{base64.b64encode(sorted_data.encode()).decode()}{api_key}".encode()).hexdigest()

        api_base = (gateway.get_setting("heleket_api_base") or "https://api.heleket.com").rstrip("/")
        response = requests.post(
            f"{api_base}/invoice/create",
            json={**data, "sign": sign},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        pay_url = payload.get("payment_url") or payload.get("pay_url") or payload.get("url")
        if not pay_url:
            raise RuntimeError("Heleket не вернул ссылку на оплату.")
        return {"payment_url": str(pay_url), "provider": "Heleket", "payment_method": "Crypto"}

    def _paritypay_signature_value(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else ""
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return format(value, "g")
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        try:
            return format(Decimal(str(value)), "f").rstrip("0").rstrip(".")
        except (InvalidOperation, ValueError):
            return str(value)

    def _build_paritypay_signature(payload: dict, secret: str) -> str:
        raw = "".join(
            _paritypay_signature_value(payload.get(key))
            for key in sorted((payload or {}).keys())
        )
        return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()

    def create_paritypay_payment(metadata: dict) -> dict:
        shop_id = (gateway.get_setting("paritypay_shop_id") or "").strip()
        api_secret = (gateway.get_setting("paritypay_api_secret_key") or "").strip()
        callback_secret = (gateway.get_setting("paritypay_callback_secret_key") or "").strip()
        paritypay_service = (gateway.get_setting("paritypay_service") or "sbp").strip().lower()
        if paritypay_service not in {"sbp", "card"}:
            paritypay_service = "sbp"
        if not shop_id or not api_secret or not callback_secret:
            raise RuntimeError("ParityPay не настроен в панели.")

        payment_id = str(metadata["payment_id"])
        panel_base = (gateway.get_setting("domain") or "").strip() or Config.SHOP_UPDATE_PANEL_URL
        callback_url = f"{panel_base.rstrip('/')}/paritypay-webhook"
        success_url = f"{public_base_url()}{url_for('keys_payment_paritypay_return', payment_id=payment_id, result='success')}"
        fail_url = f"{public_base_url()}{url_for('keys_payment_paritypay_return', payment_id=payment_id, result='fail')}"

        custom_fields = {
            "payment_id": payment_id,
            "user_id": int(metadata["user_id"]),
            "action": str(metadata["action"]),
        }
        custom_fields_json = json.dumps(custom_fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        amount_value = Decimal(str(metadata["price"])).quantize(Decimal("0.01"))
        amount_payload: int | float
        if amount_value == amount_value.to_integral():
            amount_payload = int(amount_value)
        else:
            amount_payload = float(amount_value)

        payload = {
            "shop_id": shop_id,
            "amount": amount_payload,
            "order_id": payment_id,
            "success_url": success_url,
            "fail_url": fail_url,
            "callback_url": callback_url,
            "service": paritypay_service,
            "custom_fields": custom_fields_json,
            "comment": (
                "SaulInfo: покупка ключа"
                if str(metadata.get("action") or "").strip().lower() == "purchase"
                else "SaulInfo: продление ключа"
            ),
        }
        signature = _build_paritypay_signature(payload, api_secret)
        response = requests.post(
            "https://api.paritypay.ru/invoice/create",
            json=payload,
            headers={"Content-Type": "application/json", "X-SIGNATURE": signature},
            timeout=20,
        )
        if response.status_code not in (200, 201):
            app.logger.error(
                "ParityPay invoice/create failed: status=%s body=%s payload=%s",
                response.status_code,
                response.text,
                payload,
            )
            response.raise_for_status()
        payload_json = response.json()
        pay_url = (
            payload_json.get("link")
            or payload_json.get("payment_url")
            or payload_json.get("pay_url")
            or payload_json.get("url")
        )
        if not pay_url:
            raise RuntimeError("ParityPay не вернул ссылку на оплату.")
        return {
            "payment_url": str(pay_url),
            "provider": "ParityPay",
            "payment_method": "ParityPay",
            "provider_invoice_id": str(payload_json.get("id") or payload_json.get("invoice_id") or "").strip(),
        }

    def create_yookassa_payment(payment_id: str, amount_rub: float, description: str, return_url: str) -> dict:
        shop_id = (gateway.get_setting("yookassa_shop_id") or "").strip()
        secret_key = (gateway.get_setting("yookassa_secret_key") or "").strip()
        if not shop_id or not secret_key:
            raise RuntimeError("YooKassa не настроена в панели.")

        response = requests.post(
            "https://api.yookassa.ru/v3/payments",
            json={
                "amount": {"value": f"{float(amount_rub):.2f}", "currency": "RUB"},
                "capture": True,
                "confirmation": {"type": "redirect", "return_url": return_url},
                "description": description,
            },
            headers={"Idempotence-Key": payment_id},
            auth=(shop_id, secret_key),
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        confirmation = payload.get("confirmation") or {}
        confirmation_url = confirmation.get("confirmation_url")
        if not confirmation_url:
            raise RuntimeError("YooKassa не вернула ссылку на оплату.")
        return {
            "provider_payment_id": str(payload.get("id") or ""),
            "confirmation_url": str(confirmation_url),
        }

    def verify_yookassa_payment(provider_payment_id: str) -> dict | None:
        shop_id = (gateway.get_setting("yookassa_shop_id") or "").strip()
        secret_key = (gateway.get_setting("yookassa_secret_key") or "").strip()
        if not shop_id or not secret_key or not provider_payment_id:
            return None

        try:
            response = requests.get(
                f"https://api.yookassa.ru/v3/payments/{provider_payment_id}",
                auth=(shop_id, secret_key),
                timeout=20,
            )
        except Exception:
            app.logger.exception("Failed to verify YooKassa payment %s", provider_payment_id)
            return None
        if response.status_code != 200:
            return None

        payload = response.json()
        if str(payload.get("status") or "").lower() != "succeeded":
            return None

        amount = payload.get("amount") or {}
        try:
            amount_rub = float(amount.get("value"))
        except Exception:
            amount_rub = None
        return {
            "amount": amount_rub,
            "currency_name": str(amount.get("currency") or "RUB"),
        }

    def complete_site_order(
        account: dict,
        user: dict,
        metadata: dict,
        payment_method: str,
        charge_balance: bool,
    ) -> dict:
        action = str(metadata.get("action") or "").strip().lower()
        plan_id = int(metadata.get("plan_id") or 0)
        plan = gateway.get_plan_by_id(plan_id) if plan_id else None
        if not plan:
            return {"ok": False, "message": "Тариф для операции не найден."}

        host_name = str(metadata.get("host_name") or plan.get("host_name") or "").strip()
        host = gateway.get_host(host_name)
        if not host:
            return {"ok": False, "message": "Хост для выбранного тарифа сейчас недоступен."}

        price = float(metadata.get("price") or plan.get("price") or 0)
        months = int(metadata.get("months") or plan.get("months") or 0)
        if price <= 0 or months <= 0:
            return {"ok": False, "message": "Тариф настроен некорректно и пока недоступен."}

        user_id = int(user["telegram_id"])
        username = (user.get("username") or account.get("email") or f"site_{user_id}").strip()
        balance_charged = False

        if charge_balance:
            if gateway.get_balance(user_id) < price:
                return {"ok": False, "message": "Недостаточно средств на балансе для завершения операции."}
            if not gateway.deduct_from_balance(user_id, price):
                return {"ok": False, "message": "Не удалось списать баланс для операции с ключом."}
            balance_charged = True

        def refund_if_needed():
            if balance_charged:
                gateway.add_to_balance(user_id, price)

        base_metadata = {
            "host_name": host_name,
            "plan_id": int(plan["plan_id"]),
            "plan_name": plan.get("plan_name"),
            "months": months,
            "customer_email": account.get("email"),
            "payment_method": payment_method,
        }

        if action == "purchase":
            key_email = str(metadata.get("key_email") or gateway.generate_site_key_email(account.get("email", ""), user_id))
            result = create_or_update_key_on_host(host, key_email, days_to_add=months * 30)
            if not result.get("ok"):
                refund_if_needed()
                return {"ok": False, "message": result.get("message") or "Не удалось выдать ключ на выбранном хосте."}

            new_key_id = gateway.add_new_key(
                user_id=user_id,
                host_name=host_name,
                xui_client_uuid=str(result.get("client_uuid") or ""),
                key_email=str(result.get("email") or key_email),
                expiry_timestamp_ms=int(result.get("expiry_timestamp_ms") or 0),
            )
            if not new_key_id:
                refund_if_needed()
                return {"ok": False, "message": "Ключ создался на хосте, но не сохранился в базе. Проверьте панель."}

            gateway.update_user_stats(user_id, price, months)
            gateway.log_balance_transaction(
                user_id,
                username,
                price,
                {
                    **base_metadata,
                    "action": "new",
                    "key_id": int(new_key_id),
                    "key_email": str(result.get("email") or key_email),
                },
                payment_method=payment_method,
            )
            return {
                "ok": True,
                "message": "Новый ключ успешно создан и появился в вашем кабинете.",
                "key_id": int(new_key_id),
            }

        if action == "renew":
            key_id = int(metadata.get("key_id") or 0)
            key = gateway.get_key_by_id(key_id) if key_id else None
            if not key or int(key.get("user_id") or 0) != user_id:
                refund_if_needed()
                return {"ok": False, "message": "Ключ для продления не найден."}
            if str(key.get("host_name") or "").strip() != host_name:
                refund_if_needed()
                return {"ok": False, "message": "Тариф должен принадлежать тому же хосту, что и продлеваемый ключ."}

            result = create_or_update_key_on_host(
                host,
                str(key.get("key_email") or ""),
                days_to_add=months * 30,
            )
            if not result.get("ok"):
                refund_if_needed()
                return {"ok": False, "message": result.get("message") or "Не удалось продлить ключ на выбранном хосте."}

            gateway.update_key_info(
                int(key["key_id"]),
                str(result.get("client_uuid") or key.get("xui_client_uuid") or ""),
                int(result.get("expiry_timestamp_ms") or 0),
            )
            gateway.update_user_stats(user_id, price, months)
            gateway.log_balance_transaction(
                user_id,
                username,
                price,
                {
                    **base_metadata,
                    "action": "extend",
                    "key_id": int(key["key_id"]),
                    "key_email": str(key.get("key_email") or ""),
                },
                payment_method=payment_method,
            )
            return {"ok": True, "message": "Ключ успешно продлён.", "key_id": int(key["key_id"])}

        refund_if_needed()
        return {"ok": False, "message": "Неизвестное действие для страницы ключей."}

    def handle_paid_order(account: dict | None, user: dict | None, payment_id: str, payment_method: str, verifier) -> tuple[str, str]:
        account, user = ensure_customer_context(account, user)
        if not account or not user:
            return "danger", "Сайт не смог подготовить профиль клиента для завершения оплаченного заказа."

        transaction = gateway.get_transaction_by_payment_id(payment_id)
        if not transaction:
            return "warning", "Платёж по этой ссылке не найден."

        metadata = dict(transaction.get("parsed_metadata") or {})
        if metadata.get("fulfilled"):
            return "success", "Этот оплаченный заказ уже обработан и отражён в кабинете."

        try:
            verification = verifier(metadata)
        except Exception:
            app.logger.exception("Failed to verify site payment %s", payment_id)
            verification = None
        if not verification:
            return "warning", "Платёж ещё не подтверждён платёжной системой. Проверьте статус немного позже."

        amount_rub = float(verification.get("amount") or metadata.get("price") or 0)
        if amount_rub <= 0:
            return "danger", "Платёж подтвердился без суммы. Проверьте оплату в панели."

        if not metadata.get("balance_credited"):
            if not gateway.add_to_balance(int(user["telegram_id"]), amount_rub):
                return "danger", "Платёж подтверждён, но не удалось зачислить средства на баланс клиента."
            metadata["balance_credited"] = True

        order_result = complete_site_order(account, user, metadata, payment_method=payment_method, charge_balance=True)
        gateway.finalize_pending_transaction(
            payment_id,
            payment_method,
            amount_rub=amount_rub,
            amount_currency=float(verification.get("amount") or amount_rub),
            currency_name=str(verification.get("currency_name") or "RUB"),
        )

        metadata["payment_method"] = payment_method
        metadata["verified_at"] = datetime.utcnow().isoformat(timespec="seconds")
        if order_result.get("ok"):
            metadata["fulfilled"] = True
            metadata["fulfilled_key_id"] = order_result.get("key_id")
            metadata["fulfilled_message"] = order_result.get("message")
            gateway.update_transaction_metadata(payment_id, metadata)
            return "success", str(order_result.get("message") or "Оплата подтверждена, заказ выполнен.")

        metadata["fulfilled"] = False
        metadata["fulfilment_error"] = order_result.get("message")
        gateway.update_transaction_metadata(payment_id, metadata)
        return (
            "warning",
            f"Оплата подтверждена, но операция с ключом не завершилась: {order_result.get('message')}. "
            "Средства сохранены на балансе аккаунта, можно повторить покупку или обратиться в поддержку.",
        )

    @app.context_processor
    def inject_globals():
        current_account, current_user = load_session_context()
        account_label = None
        if current_account:
            account_label = ((current_account.get("display_name") or "").strip() or current_account.get("email"))

        return {
            "brand_title": "SaulInfo",
            "current_user": current_user,
            "current_account": current_account,
            "account_label": account_label,
            "allow_self_registration": Config.ALLOW_SELF_REGISTRATION,
            "google_auth_enabled": google_auth_enabled(),
            "now": datetime.utcnow(),
        }

    def user_required(fn):
        def wrapper(*args, **kwargs):
            if "auth_user_id" not in session:
                return redirect(url_for("login_page"))
            auth_user_id = session.get("auth_user_id")
            if auth_user_id not in (None, -1):
                current_account, current_user = load_session_context()
                if not current_account:
                    session.clear()
                    flash("Доступ к сайту отозван. Войдите заново, если администратор выдаст новый доступ.", "warning")
                    return redirect(url_for("login_page"))
                if current_user and int(current_user.get("is_banned") or 0):
                    session.clear()
                    flash("Ваш доступ временно ограничен администратором.", "danger")
                    return redirect(url_for("login_page"))
            return fn(*args, **kwargs)

        wrapper.__name__ = fn.__name__
        return wrapper

    @app.route("/")
    def index():
        if session.get("auth_user_id") is not None:
            return redirect(url_for("dashboard_page"))
        return render_template("index.html")

    @app.route("/healthz")
    def healthz():
        return {"ok": True, "service": "saulinfo-site"}, 200

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        if request.method == "POST":
            email = request.form.get("email", "")
            password = request.form.get("password", "")
            account = auth_store.authenticate(email, password)
            if not account:
                flash("Неверный e-mail или пароль.", "danger")
                return render_template("login.html")
            set_authenticated_session(account)
            return redirect(url_for("dashboard_page"))

        return render_template("login.html")

    @app.get("/login/google")
    def google_login():
        if not google_auth_enabled():
            flash("Вход через Google пока не настроен.", "warning")
            return redirect(url_for("login_page"))
        if not Config.ALLOW_SELF_REGISTRATION:
            flash("Самостоятельная регистрация отключена. Доступ к сайту выдаёт администратор.", "warning")
            return redirect(url_for("login_page"))

        state = uuid.uuid4().hex
        session["google_oauth_state"] = state
        params = {
            "client_id": Config.GOOGLE_CLIENT_ID,
            "redirect_uri": google_redirect_uri(),
            "response_type": "code",
            "scope": Config.GOOGLE_OAUTH_SCOPE,
            "access_type": "online",
            "prompt": "select_account",
            "state": state,
        }
        return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")

    @app.get("/login/google/callback")
    def google_callback():
        if not google_auth_enabled():
            flash("Вход через Google пока не настроен.", "warning")
            return redirect(url_for("login_page"))

        state = (request.args.get("state") or "").strip()
        code = (request.args.get("code") or "").strip()
        expected_state = str(session.pop("google_oauth_state", "") or "")
        if not state or state != expected_state:
            flash("Не удалось подтвердить вход через Google. Попробуйте ещё раз.", "warning")
            return redirect(url_for("login_page"))
        if not code:
            flash("Google не передал код авторизации. Попробуйте ещё раз.", "warning")
            return redirect(url_for("login_page"))

        try:
            token_response = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": Config.GOOGLE_CLIENT_ID,
                    "client_secret": Config.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": google_redirect_uri(),
                    "grant_type": "authorization_code",
                },
                timeout=20,
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            access_token = str(token_payload.get("access_token") or "").strip()
            if not access_token:
                raise ValueError("Missing Google access token")

            userinfo_response = requests.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=20,
            )
            userinfo_response.raise_for_status()
            profile = userinfo_response.json()
        except Exception:
            app.logger.exception("Google OAuth flow failed")
            flash("Не удалось завершить вход через Google. Попробуйте ещё раз.", "danger")
            return redirect(url_for("login_page"))

        google_sub = str(profile.get("sub") or "").strip()
        email = str(profile.get("email") or "").strip().lower()
        display_name = str(profile.get("name") or "").strip()
        email_verified = bool(profile.get("email_verified"))

        if not google_sub or not email or not email_verified:
            flash("Google не передал подтверждённый e-mail. Используйте обычную регистрацию.", "warning")
            return redirect(url_for("login_page"))

        if not Config.ALLOW_SELF_REGISTRATION:
            existing_account = auth_store.get_user_by_email(email)
            if not existing_account:
                flash("Самостоятельная регистрация отключена. Доступ к сайту выдаёт администратор.", "warning")
                return redirect(url_for("login_page"))

        account = auth_store.create_or_update_google_user(email, google_sub, display_name)
        if not account:
            flash("Не удалось подготовить аккаунт для входа через Google.", "danger")
            return redirect(url_for("login_page"))

        set_authenticated_session(account)
        flash("Вход через Google выполнен.", "success")
        return redirect(url_for("dashboard_page"))

    @app.route("/register", methods=["GET", "POST"])
    def register_page():
        if not Config.ALLOW_SELF_REGISTRATION:
            flash("Самостоятельная регистрация отключена. Доступ к сайту выдаёт администратор.", "warning")
            return redirect(url_for("login_page"))

        if request.method == "POST":
            email = request.form.get("email", "")
            password = request.form.get("password", "")
            ok, message = auth_store.create_user(email, password)
            flash(message, "success" if ok else "warning")
            if ok:
                account = auth_store.get_user_by_email(email)
                if account:
                    set_authenticated_session(account)
                    return redirect(url_for("dashboard_page"))

        return render_template("register.html")

    @app.route("/demo-login/<int:user_id>")
    def demo_login(user_id: int):
        user = gateway.get_user(user_id)
        if not user:
            flash("Пользователь не найден в shop-update.", "warning")
            return redirect(url_for("login_page"))

        session["auth_user_id"] = -1
        session["shop_user_id"] = int(user["telegram_id"])
        return redirect(url_for("dashboard_page"))

    @app.route("/dashboard")
    @user_required
    def dashboard_page():
        account, user = load_session_context()
        account, user = ensure_portal_customer(account, user)
        payload = build_dashboard_payload(account, user)
        closed_ticket_statuses = {"closed", "resolved", "done"}
        has_open_ticket = any(
            ((ticket.get("status") or "").strip().lower() not in closed_ticket_statuses)
            for ticket in (payload.get("tickets") or [])
        )
        return render_template("dashboard.html", **build_dashboard_payload(account, user))

    @app.route("/keys", methods=["GET", "POST"])
    @user_required
    def keys_page():
        account, user = load_session_context()
        account, user = ensure_portal_customer(account, user)
        context = build_keys_context(account, user)
        if request.method == "POST":
            if not account or not user:
                flash("Сайт не смог подготовить профиль клиента для операций с ключами.", "warning")
                return redirect(url_for("keys_page"))

            action = (request.form.get("action") or "").strip().lower()
            payment_method = (request.form.get("payment_method") or "balance").strip().lower()
            available_methods = {item["code"] for item in context.get("payment_methods", [])}
            if payment_method not in available_methods:
                flash("Выбранный способ оплаты сейчас недоступен.", "warning")
                return redirect(url_for("keys_page"))

            plan_raw = (request.form.get("plan_id") or "").strip()
            if not plan_raw:
                flash("Выберите тариф для операции с ключом.", "warning")
                return redirect(url_for("keys_page"))

            try:
                plan = gateway.get_plan_by_id(int(plan_raw))
            except Exception:
                plan = None
            if not plan:
                flash("Выбранный тариф не найден.", "warning")
                return redirect(url_for("keys_page"))

            host_name = str(plan.get("host_name") or "").strip()
            host = gateway.get_host(host_name)
            if not host:
                flash("Хост для выбранного тарифа сейчас недоступен.", "danger")
                return redirect(url_for("keys_page"))

            price = float(plan.get("price") or 0)
            months = int(plan.get("months") or 0)
            if price <= 0 or months <= 0:
                flash("Тариф настроен некорректно и пока недоступен для покупки.", "danger")
                return redirect(url_for("keys_page"))

            order_metadata = {
                "action": action,
                "plan_id": int(plan["plan_id"]),
                "plan_name": plan.get("plan_name"),
                "host_name": host_name,
                "months": months,
                "price": price,
                "customer_email": account.get("email"),
                "site_auth_user_id": int(account["auth_user_id"]),
            }

            if action == "purchase":
                order_metadata["key_email"] = gateway.generate_site_key_email(account.get("email", ""), int(user["telegram_id"]))
            elif action == "renew":
                key_raw = (request.form.get("key_id") or "").strip()
                if not key_raw:
                    flash("Выберите ключ, который нужно продлить.", "warning")
                    return redirect(url_for("keys_page"))
                key = gateway.get_key_by_id(int(key_raw))
                if not key or int(key.get("user_id") or 0) != int(user["telegram_id"]):
                    flash("Ключ для продления не найден.", "warning")
                    return redirect(url_for("keys_page"))
                if str(key.get("host_name") or "").strip() != host_name:
                    flash("Тариф должен принадлежать тому же хосту, что и продлеваемый ключ.", "warning")
                    return redirect(url_for("keys_page"))
                order_metadata["key_id"] = int(key["key_id"])
                order_metadata["key_email"] = str(key.get("key_email") or "")
            else:
                flash("Неизвестное действие для страницы ключей.", "warning")
                return redirect(url_for("keys_page"))

            if payment_method == "balance":
                result = complete_site_order(account, user, order_metadata, payment_method="Balance", charge_balance=True)
                flash(result.get("message") or "Операция завершена.", "success" if result.get("ok") else "danger")
                return redirect(url_for("keys_page"))

            payment_id = str(uuid.uuid4())
            order_metadata["payment_id"] = payment_id

            if payment_method == "yookassa":
                try:
                    payment = create_yookassa_payment(
                        payment_id=payment_id,
                        amount_rub=price,
                        description=f"SaulInfo: {'покупка ключа' if action == 'purchase' else 'продление ключа'} на {months} мес.",
                        return_url=f"{public_base_url()}{url_for('keys_payment_yookassa_return', payment_id=payment_id)}",
                    )
                    order_metadata["payment_method"] = "yookassa"
                    order_metadata["provider"] = "YooKassa"
                    order_metadata["provider_payment_id"] = payment["provider_payment_id"]
                    gateway.create_pending_transaction(payment_id, int(user["telegram_id"]), price, order_metadata)
                    return redirect(payment["confirmation_url"])
                except Exception:
                    app.logger.exception("Failed to create YooKassa payment for site order")
                    flash("Не удалось создать ссылку на оплату через YooKassa.", "danger")
                    return redirect(url_for("keys_page"))

            if payment_method == "yoomoney":
                wallet = (gateway.get_setting("yoomoney_wallet") or "").strip()
                token = (gateway.get_setting("yoomoney_api_token") or "").strip()
                enabled = str(gateway.get_setting("yoomoney_enabled") or "").strip().lower() in {"1", "true", "yes", "on"}
                if not enabled or not wallet or not token:
                    flash("Оплата через ЮMoney временно недоступна.", "warning")
                    return redirect(url_for("keys_page"))

                order_metadata["payment_method"] = "yoomoney"
                order_metadata["provider"] = "YooMoney"
                gateway.create_pending_transaction(payment_id, int(user["telegram_id"]), price, order_metadata)
                return redirect(
                    build_yoomoney_quickpay_url(
                        wallet=wallet,
                        amount=price,
                        label=payment_id,
                        success_url=f"{public_base_url()}{url_for('keys_payment_yoomoney_return', payment_id=payment_id)}",
                        targets=f"SaulInfo: {'покупка ключа' if action == 'purchase' else 'продление ключа'}",
                    )
                )

            if payment_method == "cryptobot":
                try:
                    order_metadata["user_id"] = int(user["telegram_id"])
                    invoice = create_cryptobot_invoice(order_metadata)
                    order_metadata["payment_method"] = "cryptobot"
                    order_metadata["provider"] = "CryptoBot"
                    order_metadata["payment_url"] = invoice["payment_url"]
                    gateway.create_pending_transaction(payment_id, int(user["telegram_id"]), price, order_metadata)
                    gateway.update_transaction_metadata(payment_id, order_metadata)
                    return redirect(url_for("keys_payment_pending_page", payment_id=payment_id))
                except Exception:
                    app.logger.exception("Failed to create CryptoBot invoice for site order")
                    flash("Не удалось создать ссылку на оплату через CryptoBot.", "danger")
                    return redirect(url_for("keys_page"))

            if payment_method == "heleket":
                try:
                    order_metadata["user_id"] = int(user["telegram_id"])
                    payment = create_heleket_payment(order_metadata)
                    order_metadata["payment_method"] = "heleket"
                    order_metadata["provider"] = payment.get("provider") or "Heleket"
                    order_metadata["payment_url"] = payment["payment_url"]
                    gateway.create_pending_transaction(payment_id, int(user["telegram_id"]), price, order_metadata)
                    gateway.update_transaction_metadata(payment_id, order_metadata)
                    return redirect(url_for("keys_payment_pending_page", payment_id=payment_id))
                except Exception:
                    app.logger.exception("Failed to create Heleket invoice for site order")
                    flash("Не удалось создать ссылку на оплату через Heleket.", "danger")
                    return redirect(url_for("keys_page"))

            if payment_method == "paritypay":
                try:
                    order_metadata["user_id"] = int(user["telegram_id"])
                    payment = create_paritypay_payment(order_metadata)
                    order_metadata["payment_method"] = "paritypay"
                    order_metadata["provider"] = payment.get("provider") or "ParityPay"
                    order_metadata["payment_url"] = payment["payment_url"]
                    if payment.get("provider_invoice_id"):
                        order_metadata["provider_invoice_id"] = payment["provider_invoice_id"]
                    gateway.create_pending_transaction(payment_id, int(user["telegram_id"]), price, order_metadata)
                    gateway.update_transaction_metadata(payment_id, order_metadata)
                    return redirect(url_for("keys_payment_pending_page", payment_id=payment_id))
                except Exception:
                    app.logger.exception("Failed to create ParityPay invoice for site order")
                    flash("Не удалось создать ссылку на оплату через ParityPay.", "danger")
                    return redirect(url_for("keys_page"))

            if payment_method in {"ton", "tonconnect"}:
                try:
                    order_metadata["payment_method"] = "tonconnect"
                    order_metadata["provider"] = "TON Connect"
                    order_metadata["payment_url"] = build_ton_payment_url(payment_id, price)
                    gateway.create_pending_transaction(payment_id, int(user["telegram_id"]), price, order_metadata)
                    gateway.update_transaction_metadata(payment_id, order_metadata)
                    return redirect(url_for("keys_payment_pending_page", payment_id=payment_id))
                except Exception:
                    app.logger.exception("Failed to prepare TON payment for site order")
                    flash("Не удалось подготовить оплату через TON Connect.", "danger")
                    return redirect(url_for("keys_page"))

            flash("Этот способ оплаты пока не поддерживается на сайте.", "warning")
            return redirect(url_for("keys_page"))

        return render_template("keys_site.html", **context)

    @app.route("/keys/payment/pending/<payment_id>")
    @user_required
    def keys_payment_pending_page(payment_id: str):
        account, user = load_session_context()
        account, user = ensure_portal_customer(account, user)
        transaction = gateway.get_transaction_by_payment_id(payment_id)
        if not account or not user or not transaction or int(transaction.get("user_id") or 0) != int(user["telegram_id"]):
            flash("Платёж для этой сессии не найден.", "warning")
            return redirect(url_for("keys_page"))

        metadata = dict(transaction.get("parsed_metadata") or {})
        return render_template(
            "payment_pending_site.html",
            account=account,
            user=user,
            payment_id=payment_id,
            payment_url=metadata.get("payment_url"),
            payment_method=metadata.get("payment_method") or metadata.get("provider") or "External",
            check_url=url_for("keys_payment_pending_check", payment_id=payment_id),
        )

    @app.route("/keys/payment/check/<payment_id>")
    @user_required
    def keys_payment_pending_check(payment_id: str):
        account, user = load_session_context()
        account, user = ensure_portal_customer(account, user)
        transaction = gateway.get_transaction_by_payment_id(payment_id)
        if not account or not user or not transaction or int(transaction.get("user_id") or 0) != int(user["telegram_id"]):
            flash("Платёж для этой сессии не найден.", "warning")
            return redirect(url_for("keys_page"))

        metadata = dict(transaction.get("parsed_metadata") or {})
        if str(transaction.get("status") or "").strip().lower() == "paid":
            if metadata.get("fulfilled"):
                flash(
                    "Платёж уже подтверждён, а доступ отражён в кабинете. Если ключ не виден сразу, обновите страницу.",
                    "success",
                )
                return redirect(url_for("keys_page"))

            payment_method = str(metadata.get("payment_method") or metadata.get("provider") or "External")
            category, message = handle_paid_order(
                account,
                user,
                payment_id,
                payment_method,
                lambda _metadata, amount=float(transaction.get("amount_rub") or metadata.get("price") or 0): {
                    "amount": amount,
                    "currency_name": "RUB",
                },
            )
            flash(message, category)
            return redirect(url_for("keys_page"))
            flash("Платёж подтверждён. Если доступ уже создан панелью, он появится в списке ключей после обновления страницы.", "success")
            return redirect(url_for("keys_page"))

        flash("Платёж ещё не подтверждён. Если вы уже оплатили, проверьте статус немного позже.", "warning")
        return redirect(url_for("keys_payment_pending_page", payment_id=payment_id))

    @app.route("/keys/payment/yookassa/return")
    @user_required
    def keys_payment_yookassa_return():
        payment_id = (request.args.get("payment_id") or "").strip()
        account, user = load_session_context()
        category, message = handle_paid_order(
            account,
            user,
            payment_id,
            "YooKassa",
            lambda metadata: verify_yookassa_payment(str(metadata.get("provider_payment_id") or "")),
        )
        flash(message, category)
        return redirect(url_for("keys_page"))

    @app.route("/keys/payment/yoomoney/return")
    @user_required
    def keys_payment_yoomoney_return():
        payment_id = (request.args.get("payment_id") or "").strip()
        account, user = load_session_context()
        category, message = handle_paid_order(
            account,
            user,
            payment_id,
            "YooMoney",
            lambda metadata: find_yoomoney_payment(str(metadata.get("payment_id") or payment_id)),
        )
        flash(message, category)
        return redirect(url_for("keys_page"))

    @app.route("/keys/payment/paritypay/return/<payment_id>")
    @user_required
    def keys_payment_paritypay_return(payment_id: str):
        result = (request.args.get("result") or "").strip().lower()
        if result == "fail":
            flash("Оплата через ParityPay не завершена. Если деньги уже списались, проверьте статус чуть позже.", "warning")
            return redirect(url_for("keys_page"))
        flash("Платёж принят. После подтверждения ParityPay доступ появится в кабинете автоматически.", "success")
        return redirect(url_for("keys_payment_pending_page", payment_id=payment_id))

    @app.route("/support/media/<int:ticket_id>/<path:filename>")
    @user_required
    def support_media_file(ticket_id: int, filename: str):
        account, user = load_session_context()
        if not user:
            abort(404)
        ticket = next(
            (
                item
                for item in safe_gateway_call("user_tickets_media", lambda: gateway.get_user_tickets(int(user["telegram_id"])), [])
                if int(item.get("ticket_id") or 0) == int(ticket_id)
            ),
            None,
        )
        if not ticket:
            abort(404)
        safe_name = Path(filename).name
        ticket_dir = (gateway.support_media_dir / f"ticket_{int(ticket_id)}").resolve()
        target = (ticket_dir / safe_name).resolve()
        if not str(target).startswith(str(ticket_dir)) or not target.exists():
            abort(404)
        return send_file(target)

    @app.route("/support", methods=["GET", "POST"])
    @user_required
    def support_page():
        account, user = load_session_context()
        account, user = ensure_portal_customer(account, user)
        payload = build_dashboard_payload(account, user)
        closed_ticket_statuses = {"closed", "resolved", "done"}
        has_open_ticket = any(
            ((ticket.get("status") or "").strip().lower() not in closed_ticket_statuses)
            for ticket in (payload.get("tickets") or [])
        )
        if request.method == "POST":
            if not account or not user:
                flash("Сайт не смог подготовить клиентский профиль для поддержки. Попробуйте выйти и войти заново.", "warning")
                return redirect(url_for("support_page"))

            message = request.form.get("message", "")
            uploaded_files = [
                storage
                for storage in request.files.getlist("photos")
                if storage and str(storage.filename or "").strip()
            ]
            uploaded_files = [
                storage
                for storage in uploaded_files
                if Path(str(storage.filename or "")).suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
            ]
            if not (message or "").strip() and not uploaded_files:
                flash("Опишите обращение, чтобы поддержка получила текст заявки.", "warning")
                return redirect(url_for("support_page"))

            action = (request.form.get("action") or "new").strip().lower()
            if action == "reply":
                ticket_raw = (request.form.get("ticket_id") or "").strip()
                if not ticket_raw.isdigit():
                    flash("Не удалось определить обращение для ответа.", "warning")
                    return redirect(url_for("support_page"))

                ok = safe_gateway_call(
                    "add_support_reply",
                    lambda: gateway.add_support_reply(
                        int(user["telegram_id"]),
                        int(ticket_raw),
                        message,
                        gateway.save_support_media(int(ticket_raw), uploaded_files),
                    ),
                    False,
                )
                if ok:
                    flash(f"Ответ добавлен в обращение #{ticket_raw}.", "success")
                else:
                    flash("Не удалось отправить ответ в это обращение. Возможно, оно уже закрыто.", "warning")
            else:
                if has_open_ticket:
                    flash("Пока у вас есть открытое обращение, новое создать нельзя. Продолжите текущий диалог ниже.", "warning")
                    return redirect(url_for("support_page"))
                subject = request.form.get("subject", "")
                ticket_id = safe_gateway_call(
                    "create_support_ticket",
                    lambda: gateway.create_support_ticket(
                        int(user["telegram_id"]),
                        subject,
                        message if (message or "").strip() else "Пользователь отправил фото.",
                    ),
                    None,
                )
                if ticket_id:
                    if uploaded_files:
                        media_records = gateway.save_support_media(int(ticket_id), uploaded_files)
                        if media_records:
                            safe_gateway_call(
                                "attach_support_media",
                                lambda: gateway.add_support_reply(int(user["telegram_id"]), int(ticket_id), "", media_records),
                                False,
                            )
                    flash(f"Обращение #{ticket_id} отправлено в поддержку.", "success")
                else:
                    flash("Не удалось создать обращение. Попробуйте ещё раз.", "danger")
            return redirect(url_for("support_page"))
        if account and payload.get("support_has_unread"):
            try:
                auth_store.mark_support_seen(int(account["auth_user_id"]))
                account = auth_store.get_user(int(account["auth_user_id"])) or account
                payload["account"] = account
                payload["support_has_unread"] = False
            except Exception:
                app.logger.exception("Failed to mark support as seen for auth user %s", account.get("auth_user_id"))
        return render_template("support_site.html", has_open_ticket=has_open_ticket, **payload)

    @app.route("/profile", methods=["GET", "POST"])
    @user_required
    def profile_page():
        account, user = load_session_context()
        if not account:
            flash("Аккаунт сайта не найден.", "warning")
            return redirect(url_for("logout_page"))

        if request.method == "POST":
            action = (request.form.get("action") or "").strip()
            auth_user_id = int(account["auth_user_id"])

            if action == "profile":
                ok, message = auth_store.update_profile(auth_user_id, request.form.get("display_name", ""))
                flash(message, "success" if ok else "warning")
                return redirect(url_for("profile_page"))

            if action == "password":
                new_password = request.form.get("new_password", "")
                confirmation = request.form.get("confirm_password", "")
                if new_password != confirmation:
                    flash("Подтверждение пароля не совпадает.", "warning")
                    return redirect(url_for("profile_page"))

                ok, message = auth_store.change_password(
                    auth_user_id,
                    request.form.get("current_password", ""),
                    new_password,
                )
                flash(message, "success" if ok else "warning")
                return redirect(url_for("profile_page"))

        account, user = load_session_context()
        return render_template("profile.html", **build_dashboard_payload(account, user))

    @app.post("/logout")
    def logout_page():
        session.pop("auth_user_id", None)
        session.pop("shop_user_id", None)
        flash("Вы вышли из кабинета.", "success")
        return redirect(url_for("index"))

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.exception("Unhandled SaulInfo site error: %s", error)
        had_session = session.get("auth_user_id") is not None or session.get("shop_user_id") is not None
        if had_session:
            session.pop("auth_user_id", None)
            session.pop("shop_user_id", None)
            flash("Сайт временно перезапустил пользовательскую сессию после внутренней ошибки. Попробуйте открыть страницу ещё раз.", "warning")
            return redirect(url_for("index"))
        return (
            render_template("index.html"),
            500,
        )

    return app


def main():
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=False)


if __name__ == "__main__":
    main()

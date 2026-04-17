from datetime import datetime

from flask import Flask, flash, redirect, render_template, request, session, url_for

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

    def load_session_context() -> tuple[dict | None, dict | None]:
        current_user = None
        current_account = None
        auth_user_id = session.get("auth_user_id")
        shop_user_id = session.get("shop_user_id")

        if auth_user_id not in (None, -1):
            try:
                current_account = auth_store.get_user(int(auth_user_id))
            except Exception:
                current_account = None

            if current_account and shop_user_id is None:
                linked_shop_user_id = current_account.get("linked_shop_user_id")
                if linked_shop_user_id is None:
                    try:
                        current_user = gateway.ensure_site_customer(
                            int(current_account["auth_user_id"]),
                            current_account.get("email", ""),
                            current_account.get("display_name"),
                        )
                    except Exception:
                        current_user = None
                    if current_user:
                        session["shop_user_id"] = int(current_user["telegram_id"])
                        auth_store.link_shop_user(int(current_account["auth_user_id"]), int(current_user["telegram_id"]))
                        shop_user_id = int(current_user["telegram_id"])
                        current_account = auth_store.get_user(int(current_account["auth_user_id"]))
                else:
                    shop_user_id = int(linked_shop_user_id)

        if shop_user_id is not None:
            try:
                current_user = gateway.get_user(int(shop_user_id))
            except Exception:
                current_user = None

        if current_account and current_user is None and auth_user_id not in (None, -1):
            try:
                current_user = gateway.ensure_site_customer(
                    int(current_account["auth_user_id"]),
                    current_account.get("email", ""),
                    current_account.get("display_name"),
                )
            except Exception:
                current_user = None
            if current_user:
                session["shop_user_id"] = int(current_user["telegram_id"])
                auth_store.link_shop_user(int(current_account["auth_user_id"]), int(current_user["telegram_id"]))

        return current_account, current_user

    def safe_gateway_call(label: str, fn, fallback):
        try:
            return fn()
        except Exception:
            app.logger.exception("SaulInfo gateway call failed: %s", label)
            return fallback

    def build_dashboard_payload(account: dict | None, user: dict | None) -> dict:
        user_id = int(user["telegram_id"]) if user else None
        keys = safe_gateway_call("user_keys", lambda: gateway.get_user_keys(user_id), []) if user_id is not None else []
        tickets = safe_gateway_call("user_tickets", lambda: gateway.get_user_tickets(user_id), []) if user_id is not None else []
        referrals = safe_gateway_call("referrals", lambda: gateway.get_referrals(user_id), []) if user_id is not None else []
        hosts = safe_gateway_call("hosts_with_plans", gateway.get_hosts_with_plans, [])
        ticket_threads = {
            int(ticket.get("ticket_id")): safe_gateway_call(
                f"ticket_messages_{ticket.get('ticket_id')}",
                lambda ticket_id=int(ticket.get("ticket_id")): gateway.get_ticket_messages(ticket_id),
                [],
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
        }

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
            "now": datetime.utcnow(),
        }

    def user_required(fn):
        def wrapper(*args, **kwargs):
            if "auth_user_id" not in session:
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

            session["auth_user_id"] = int(account["auth_user_id"])
            linked_shop_user_id = account.get("linked_shop_user_id")
            if linked_shop_user_id is not None:
                session["shop_user_id"] = int(linked_shop_user_id)
            else:
                session.pop("shop_user_id", None)

            return redirect(url_for("dashboard_page"))

        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register_page():
        if not Config.ALLOW_SELF_REGISTRATION:
            flash("Самостоятельная регистрация отключена. Доступ к сайту выдаёт администратор.", "warning")
            return redirect(url_for("login_page"))

        if request.method == "POST":
            email = request.form.get("email", "")
            password = request.form.get("password", "")
            linked_raw = (request.form.get("linked_shop_user_id") or "").strip()
            linked_shop_user_id = None

            if linked_raw:
                try:
                    linked_shop_user_id = int(linked_raw)
                except ValueError:
                    flash("ID пользователя должен быть числом.", "warning")
                    return render_template("register.html")

                if not gateway.user_exists(linked_shop_user_id):
                    flash("Пользователь с таким ID в shop-update не найден.", "warning")
                    return render_template("register.html")

            ok, message = auth_store.create_user(email, password, linked_shop_user_id)
            flash(message, "success" if ok else "warning")
            if ok:
                return redirect(url_for("login_page"))

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
        return render_template("dashboard.html", **build_dashboard_payload(account, user))

    @app.route("/keys", methods=["GET", "POST"])
    @user_required
    def keys_page():
        account, user = load_session_context()
        if request.method == "POST":
            if not account or not user:
                flash("Сайт не смог подготовить профиль клиента для операций с ключами.", "warning")
                return redirect(url_for("keys_page"))

            action = (request.form.get("action") or "").strip()
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

            host_name = plan.get("host_name")
            host = gateway.get_host(host_name)
            if not host:
                flash("Хост для выбранного тарифа сейчас недоступен.", "danger")
                return redirect(url_for("keys_page"))

            price = float(plan.get("price") or 0)
            months = int(plan.get("months") or 0)
            user_id = int(user["telegram_id"])
            username = (user.get("username") or account.get("email") or f"site_{user_id}").strip()

            if price <= 0 or months <= 0:
                flash("Тариф настроен некорректно и пока недоступен для покупки.", "danger")
                return redirect(url_for("keys_page"))

            if gateway.get_balance(user_id) < price:
                flash("Недостаточно средств на балансе для этой операции.", "warning")
                return redirect(url_for("keys_page"))

            if action == "purchase":
                key_email = gateway.generate_site_key_email(account.get("email", ""), user_id)
                if not gateway.deduct_from_balance(user_id, price):
                    flash("Не удалось списать баланс для покупки ключа.", "danger")
                    return redirect(url_for("keys_page"))

                result = create_or_update_key_on_host(host, key_email, days_to_add=months * 30)
                if not result.get("ok"):
                    gateway.add_to_balance(user_id, price)
                    flash(result.get("message") or "Не удалось выдать ключ на выбранном хосте.", "danger")
                    return redirect(url_for("keys_page"))

                new_key_id = gateway.add_new_key(
                    user_id=user_id,
                    host_name=str(host_name),
                    xui_client_uuid=str(result.get("client_uuid") or ""),
                    key_email=str(result.get("email") or key_email),
                    expiry_timestamp_ms=int(result.get("expiry_timestamp_ms") or 0),
                )
                if not new_key_id:
                    gateway.add_to_balance(user_id, price)
                    flash("Ключ создался на хосте, но не сохранился в базе. Проверьте панель.", "danger")
                    return redirect(url_for("keys_page"))

                gateway.update_user_stats(user_id, price, months)
                gateway.log_balance_transaction(
                    user_id,
                    username,
                    price,
                    {
                        "action": "new",
                        "host_name": host_name,
                        "plan_id": int(plan["plan_id"]),
                        "plan_name": plan.get("plan_name"),
                        "months": months,
                        "key_id": int(new_key_id),
                        "customer_email": account.get("email"),
                    },
                )
                flash("Новый ключ успешно создан и появился в вашем кабинете.", "success")
                return redirect(url_for("keys_page"))

            if action == "renew":
                key_raw = (request.form.get("key_id") or "").strip()
                if not key_raw:
                    flash("Выберите ключ, который нужно продлить.", "warning")
                    return redirect(url_for("keys_page"))

                key = gateway.get_key_by_id(int(key_raw))
                if not key or int(key.get("user_id") or 0) != user_id:
                    flash("Ключ для продления не найден.", "warning")
                    return redirect(url_for("keys_page"))
                if str(key.get("host_name") or "").strip() != str(host_name or "").strip():
                    flash("Тариф должен принадлежать тому же хосту, что и продлеваемый ключ.", "warning")
                    return redirect(url_for("keys_page"))

                if not gateway.deduct_from_balance(user_id, price):
                    flash("Не удалось списать баланс для продления ключа.", "danger")
                    return redirect(url_for("keys_page"))

                result = create_or_update_key_on_host(
                    host,
                    str(key.get("key_email") or ""),
                    days_to_add=months * 30,
                )
                if not result.get("ok"):
                    gateway.add_to_balance(user_id, price)
                    flash(result.get("message") or "Не удалось продлить ключ на выбранном хосте.", "danger")
                    return redirect(url_for("keys_page"))

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
                        "action": "extend",
                        "host_name": host_name,
                        "plan_id": int(plan["plan_id"]),
                        "plan_name": plan.get("plan_name"),
                        "months": months,
                        "key_id": int(key["key_id"]),
                        "customer_email": account.get("email"),
                    },
                )
                flash("Ключ успешно продлён.", "success")
                return redirect(url_for("keys_page"))

            flash("Неизвестное действие для страницы ключей.", "warning")
            return redirect(url_for("keys_page"))

        return render_template("keys_v3.html", **build_dashboard_payload(account, user))

    @app.route("/support", methods=["GET", "POST"])
    @user_required
    def support_page():
        account, user = load_session_context()
        if request.method == "POST":
            if not user:
                flash("Для отправки обращения нужна привязка к аккаунту клиента.", "warning")
                return redirect(url_for("support_page"))

            subject = request.form.get("subject", "")
            message = request.form.get("message", "")
            if not (message or "").strip():
                flash("Опишите обращение, чтобы поддержка получила текст заявки.", "warning")
                return redirect(url_for("support_page"))

            ticket_id = safe_gateway_call(
                "create_support_ticket",
                lambda: gateway.create_support_ticket(int(user["telegram_id"]), subject, message),
                None,
            )
            if ticket_id:
                flash(f"Обращение #{ticket_id} отправлено в поддержку.", "success")
            else:
                flash("Не удалось создать обращение. Попробуйте ещё раз.", "danger")
            return redirect(url_for("support_page"))
        return render_template("support_v2.html", **build_dashboard_payload(account, user))

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

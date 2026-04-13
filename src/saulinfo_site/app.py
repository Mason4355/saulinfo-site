from datetime import datetime

from flask import Flask, flash, redirect, render_template, request, session, url_for

from saulinfo_site.auth_store import AuthStore
from saulinfo_site.config import Config
from saulinfo_site.gateway import ShopUpdateGateway


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

        if shop_user_id is not None:
            try:
                current_user = gateway.get_user(int(shop_user_id))
            except Exception:
                current_user = None

        return current_account, current_user

    def build_dashboard_payload(account: dict | None, user: dict | None) -> dict:
        user_id = int(user["telegram_id"]) if user else None
        keys = gateway.get_user_keys(user_id) if user_id is not None else []
        tickets = gateway.get_user_tickets(user_id) if user_id is not None else []
        referrals = gateway.get_referrals(user_id) if user_id is not None else []
        hosts = gateway.get_hosts_with_plans()

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

    @app.route("/keys")
    @user_required
    def keys_page():
        account, user = load_session_context()
        return render_template("keys.html", **build_dashboard_payload(account, user))

    @app.route("/support", methods=["GET", "POST"])
    @user_required
    def support_page():
        account, user = load_session_context()
        if request.method == "POST":
            flash("Новая форма веб-обращений будет подключена следующим этапом. История обращений уже доступна ниже.", "warning")
            return redirect(url_for("support_page"))
        return render_template("support.html", **build_dashboard_payload(account, user))

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

    return app


def main():
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=False)


if __name__ == "__main__":
    main()

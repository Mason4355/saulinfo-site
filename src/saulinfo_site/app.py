import secrets
from datetime import datetime

from flask import Flask, flash, redirect, render_template, request, session, url_for

from saulinfo_site.config import Config
from saulinfo_site.gateway import ShopUpdateGateway
from saulinfo_site.vk_auth import get_vk_auth_url


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)

    gateway = ShopUpdateGateway()

    @app.context_processor
    def inject_globals():
        current_user = None
        user_id = session.get("user_id")
        if user_id is not None:
            try:
                current_user = gateway.get_user(int(user_id))
            except Exception:
                current_user = None
        return {
            "brand_title": "SaulInfo",
            "current_user": current_user,
            "now": datetime.utcnow(),
        }

    def user_required(fn):
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login_page"))
            return fn(*args, **kwargs)

        wrapper.__name__ = fn.__name__
        return wrapper

    @app.route("/")
    def index():
        if session.get("user_id"):
            return redirect(url_for("dashboard_page"))
        return render_template("index.html")

    @app.route("/login")
    def login_page():
        vk_ready = bool(Config.VK_CLIENT_ID and Config.VK_CLIENT_SECRET and Config.VK_REDIRECT_URI)
        return render_template("login.html", vk_ready=vk_ready)

    @app.route("/auth/vk")
    def vk_start():
        if not (Config.VK_CLIENT_ID and Config.VK_CLIENT_SECRET and Config.VK_REDIRECT_URI):
            flash("VK OAuth ещё не настроен.", "warning")
            return redirect(url_for("login_page"))
        state = secrets.token_urlsafe(24)
        session["vk_state"] = state
        return redirect(get_vk_auth_url(state))

    @app.route("/auth/vk/callback")
    def vk_callback():
        state = request.args.get("state", "")
        if not state or state != session.get("vk_state"):
            flash("Сессия VK-входа устарела.", "warning")
            return redirect(url_for("login_page"))
        session.pop("vk_state", None)

        code = request.args.get("code", "")
        if not code:
            flash("VK не вернул код авторизации.", "danger")
            return redirect(url_for("login_page"))

        flash("Каркас VK callback готов. Следующий шаг — обменивать code на access_token и поднимать профиль пользователя.", "success")
        return redirect(url_for("login_page"))

    @app.route("/demo-login/<int:user_id>")
    def demo_login(user_id: int):
        user = gateway.get_user(user_id)
        if not user:
            flash("Пользователь не найден в shop-update.", "warning")
            return redirect(url_for("login_page"))
        session["user_id"] = int(user["telegram_id"])
        return redirect(url_for("dashboard_page"))

    @app.route("/dashboard")
    @user_required
    def dashboard_page():
        user = gateway.get_user(int(session["user_id"]))
        if not user:
            session.pop("user_id", None)
            flash("Пользователь не найден.", "warning")
            return redirect(url_for("login_page"))
        return render_template(
            "dashboard.html",
            user=user,
            keys=gateway.get_user_keys(int(user["telegram_id"])),
            tickets=gateway.get_user_tickets(int(user["telegram_id"])),
            referrals=gateway.get_referrals(int(user["telegram_id"])),
            hosts=gateway.get_hosts_with_plans(),
        )

    @app.post("/logout")
    def logout_page():
        session.pop("user_id", None)
        flash("Вы вышли из кабинета.", "success")
        return redirect(url_for("index"))

    return app


def main():
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=False)


if __name__ == "__main__":
    main()

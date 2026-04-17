import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

from py3xui import Api, Client, Inbound


def _normalize_panel_host_url(host_url: str | None) -> str:
    raw = (host_url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path
    path = parsed.path if parsed.netloc else ""
    return f"{scheme}://{netloc}{path.rstrip('/')}"


def _login_to_host(host: dict) -> tuple[Api | None, Inbound | None, str | None]:
    normalized_host_url = _normalize_panel_host_url(host.get("host_url"))
    normalized_username = (host.get("host_username") or "").strip()
    normalized_password = host.get("host_pass") or ""
    inbound_id = host.get("host_inbound_id")

    if not normalized_host_url:
        return None, None, "У хоста не заполнен URL панели XUI."
    if not normalized_username:
        return None, None, "У хоста не заполнен логин панели XUI."
    if inbound_id in ("", None):
        return None, None, "У хоста не указан inbound ID."

    try:
        api = Api(host=normalized_host_url, username=normalized_username, password=normalized_password)
        api.login()
        inbounds = api.inbound.get_list()
        inbound = next((item for item in inbounds if str(getattr(item, "id", "")) == str(int(inbound_id))), None)
        if inbound is None:
            return None, None, f"Inbound ID {inbound_id} не найден на хосте."
        return api, inbound, None
    except Exception as exc:
        return None, None, f"Не удалось подключиться к панели XUI: {exc}"


def _update_or_create_client(api: Api, inbound_id: int, email: str, days_to_add: int | None = None, target_expiry_ms: int | None = None) -> tuple[str | None, int | None]:
    try:
        inbound_to_modify = api.inbound.get_by_id(inbound_id)
        if not inbound_to_modify:
            return None, None
        if inbound_to_modify.settings.clients is None:
            inbound_to_modify.settings.clients = []

        existing_index = -1
        for index, client in enumerate(inbound_to_modify.settings.clients):
            if client.email == email:
                existing_index = index
                break

        if target_expiry_ms is not None:
            new_expiry_ms = int(target_expiry_ms)
        else:
            normalized_days = int(days_to_add or 0)
            if normalized_days <= 0:
                return None, None
            if existing_index != -1 and inbound_to_modify.settings.clients[existing_index].expiry_time > int(datetime.now().timestamp() * 1000):
                current_expiry = datetime.fromtimestamp(inbound_to_modify.settings.clients[existing_index].expiry_time / 1000)
                new_expiry = current_expiry + timedelta(days=normalized_days)
            else:
                new_expiry = datetime.now() + timedelta(days=normalized_days)
            new_expiry_ms = int(new_expiry.timestamp() * 1000)

        if existing_index != -1:
            existing = inbound_to_modify.settings.clients[existing_index]
            existing.enable = True
            existing.expiry_time = new_expiry_ms
            client_uuid = existing.id
        else:
            client_uuid = str(uuid.uuid4())
            inbound_to_modify.settings.clients.append(
                Client(
                    id=client_uuid,
                    email=email,
                    enable=True,
                    flow="xtls-rprx-vision",
                    expiry_time=new_expiry_ms,
                )
            )

        api.inbound.update(inbound_id, inbound_to_modify)
        return client_uuid, new_expiry_ms
    except Exception:
        return None, None


def _subscription_link(client_uuid: str, host: dict) -> str | None:
    host_url = _normalize_panel_host_url(host.get("host_url"))
    parsed = urlparse(host_url)
    if not parsed.hostname:
        return None
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    return f"{scheme}://{parsed.hostname}/sub/{client_uuid}?format=v2ray"


def create_or_update_key_on_host(host: dict, email: str, days_to_add: int | None = None, expiry_timestamp_ms: int | None = None) -> dict:
    api, inbound, login_error = _login_to_host(host)
    if not api or not inbound:
        return {"ok": False, "message": login_error or "Не удалось подключиться к XUI."}

    client_uuid, expiry_ms = _update_or_create_client(
        api,
        int(inbound.id),
        email,
        days_to_add=days_to_add,
        target_expiry_ms=expiry_timestamp_ms,
    )
    if not client_uuid or not expiry_ms:
        return {"ok": False, "message": "XUI не смогла создать или обновить клиентский доступ."}

    return {
        "ok": True,
        "client_uuid": client_uuid,
        "email": email,
        "expiry_timestamp_ms": expiry_ms,
        "connection_string": _subscription_link(client_uuid, host),
    }

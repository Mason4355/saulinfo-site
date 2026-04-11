from urllib.parse import urlencode

from saulinfo_site.config import Config


def get_vk_auth_url(state: str) -> str:
    params = {
        "client_id": Config.VK_CLIENT_ID,
        "redirect_uri": Config.VK_REDIRECT_URI,
        "response_type": "code",
        "scope": "email",
        "state": state,
        "v": "5.199",
    }
    return f"https://oauth.vk.com/authorize?{urlencode(params)}"

import json
import os
from typing import Dict, Optional

AUTH_FILE = "user_credentials.json"

class AuthState:
    UNAUTHENTICATED = "unauthenticated"
    WAITING_USERNAME = "waiting_username"
    WAITING_PASSWORD = "waiting_password"
    AUTHENTICATED = "authenticated"

# Memory state for login flows
# { user_id: {"state": AuthState, "username": "temp_username"} }
_login_flows: Dict[int, Dict[str, str]] = {}

def _load_credentials() -> Dict[str, Dict[str, str]]:
    if not os.path.exists(AUTH_FILE):
        return {}
    try:
        with open(AUTH_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_credentials(data: Dict[str, Dict[str, str]]) -> None:
    with open(AUTH_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_credentials(user_id: int) -> Optional[Dict[str, str]]:
    """Return stored credentials for user_id if they exist."""
    data = _load_credentials()
    return data.get(str(user_id))

def is_authenticated(user_id: int) -> bool:
    """Check if the user has valid stored credentials."""
    return get_credentials(user_id) is not None

def save_user_credentials(user_id: int, username: str, password: str) -> None:
    """Save credentials to disk."""
    data = _load_credentials()
    data[str(user_id)] = {"username": username, "password": password}
    _save_credentials(data)

def remove_credentials(user_id: int) -> None:
    """Log the user out by removing credentials."""
    data = _load_credentials()
    if str(user_id) in data:
        del data[str(user_id)]
        _save_credentials(data)
    if user_id in _login_flows:
        del _login_flows[user_id]

def get_flow_state(user_id: int) -> str:
    """Get the current login flow state."""
    if is_authenticated(user_id):
        return AuthState.AUTHENTICATED
    
    # Force memory state reset if credentials no longer exist on disk
    if user_id in _login_flows and _login_flows[user_id].get("state") == AuthState.AUTHENTICATED:
        _login_flows[user_id]["state"] = AuthState.UNAUTHENTICATED

    if user_id not in _login_flows:
        return AuthState.UNAUTHENTICATED
    return _login_flows[user_id].get("state", AuthState.UNAUTHENTICATED)

def set_flow_state(user_id: int, state: str, username: Optional[str] = None) -> None:
    """Set the login flow state."""
    if user_id not in _login_flows:
        _login_flows[user_id] = {}
    _login_flows[user_id]["state"] = state
    if username is not None:
        _login_flows[user_id]["username"] = username

def get_flow_username(user_id: int) -> Optional[str]:
    """Get the temporarily stored username during login flow."""
    if user_id in _login_flows:
        return _login_flows[user_id].get("username")
    return None

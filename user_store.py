import hashlib, json
from pathlib import Path

USERS_PATH = Path("logs/customer_users.json")

def _hash(value): return hashlib.sha256(value.encode("utf-8")).hexdigest()

def _load():
    USERS_PATH.parent.mkdir(exist_ok=True)
    if not USERS_PATH.exists():
        _save([{"name": "Demo User", "mobile": "9999999999", "password_hash": _hash("demo123"), "enabled": True}])
    return json.loads(USERS_PATH.read_text(encoding="utf-8"))

def _save(users): USERS_PATH.write_text(json.dumps(users, indent=2), encoding="utf-8")

def list_users(): return _load()

def authenticate(mobile, password):
    for user in _load():
        if user["mobile"] == mobile and user["password_hash"] == _hash(password) and user.get("enabled", True): return user
    return None

def add_user(name, mobile, password):
    users = _load()
    if any(u["mobile"] == mobile for u in users): return False
    users.append({"name": name, "mobile": mobile, "password_hash": _hash(password), "enabled": True}); _save(users); return True

def set_enabled(mobile, enabled):
    users = _load(); changed = False
    for user in users:
        if user["mobile"] == mobile: user["enabled"] = enabled; changed = True
    if changed: _save(users)
    return changed

def reset_password(mobile, new_password):
    users = _load(); changed = False
    for user in users:
        if user["mobile"] == mobile:
            user["password_hash"] = _hash(new_password)
            changed = True
    if changed: _save(users)
    return changed

def mask_mobile(mobile): return "*" * max(0, len(mobile) - 4) + mobile[-4:]

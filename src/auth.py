"""
auth.py
-------
Minimal auth για το Streamlit app. Hardcoded users σε YAML config.

Δομή του secrets.toml ή .streamlit/secrets.toml:

    [auth]
    cookie_name = "myteam_ai_agent_auth"
    cookie_key = "tuxairo-tucha-randomstring-min-32-chars"
    cookie_expiry_days = 7

    [[auth.users]]
    username = "dimitris"
    name = "Dimitris"
    password_hash = "$2b$12$..."  # bcrypt — δες παρακάτω για παραγωγή
    role = "admin"

    [[auth.users]]
    username = "manager_glyfada"
    name = "Manager Γλυφάδας"
    password_hash = "$2b$12$..."
    role = "user"

Παραγωγή bcrypt hash:
    python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import bcrypt
import streamlit as st


@dataclass
class AuthUser:
    username: str
    name: str
    role: str  # 'admin' | 'user'


def _get_users() -> list[dict]:
    """Διαβάζει τους χρήστες από st.secrets['auth']['users']."""
    auth = st.secrets.get("auth", {})
    return list(auth.get("users", []))


def _verify(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def login_gate() -> Optional[AuthUser]:
    """
    Καλείται στην αρχή κάθε σελίδας. Αν ο χρήστης δεν είναι logged in,
    εμφανίζει login form και κάνει st.stop().
    Αλλιώς επιστρέφει AuthUser.
    """
    if "auth_user" in st.session_state and st.session_state.auth_user:
        return st.session_state.auth_user

    users = _get_users()
    if not users:
        st.error(
            "⚠️ Δεν υπάρχει διαμόρφωση auth στο secrets. Πρόσθεσε `[[auth.users]]` "
            "στο `.streamlit/secrets.toml` (δες auth_config.example.toml)."
        )
        st.stop()

    st.markdown("### 🔐 Σύνδεση")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        ok = st.form_submit_button("Σύνδεση")

    if ok:
        for u in users:
            if u["username"] == username and _verify(password, u["password_hash"]):
                st.session_state.auth_user = AuthUser(
                    username=u["username"], name=u["name"], role=u.get("role", "user")
                )
                st.rerun()
        st.error("Λάθος username ή password")

    st.stop()
    return None  # unreachable


def logout_button() -> None:
    """Render-άρει logout button στο sidebar."""
    if "auth_user" in st.session_state and st.session_state.auth_user:
        user = st.session_state.auth_user
        st.sidebar.markdown(f"👤 **{user.name}** _{user.role}_")
        if st.sidebar.button("Logout", use_container_width=True):
            del st.session_state.auth_user
            st.rerun()


def require_admin(user: Optional[AuthUser]) -> AuthUser:
    if not user or user.role != "admin":
        st.error("Αυτή η σελίδα απαιτεί admin access.")
        st.stop()
    return user

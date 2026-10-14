"""Server-side Firestore storage for Contact Us messages."""

import os
import threading
from typing import Any, Dict

from backend import config

COLLECTION = "contact_requests"
_lock = threading.Lock()
_firebase_app = None
_firestore_client = None


class InboxUnavailable(RuntimeError):
    pass


def firestore_configured() -> bool:
    if config.FIREBASE_SERVICE_ACCOUNT:
        service_account_available = os.path.isfile(config.FIREBASE_SERVICE_ACCOUNT)
    else:
        application_credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        service_account_available = bool(application_credentials and os.path.isfile(application_credentials))
    return bool(config.FIREBASE_PROJECT_ID and service_account_available)


def _clients():
    global _firebase_app, _firestore_client
    if not firestore_configured():
        raise InboxUnavailable("The Firestore inbox is not configured yet.")
    if _firestore_client is not None:
        return _firebase_app, _firestore_client
    with _lock:
        if _firestore_client is not None:
            return _firebase_app, _firestore_client
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore

            try:
                app = firebase_admin.get_app("deltavox-inbox")
            except ValueError:
                if config.FIREBASE_SERVICE_ACCOUNT:
                    credential = credentials.Certificate(config.FIREBASE_SERVICE_ACCOUNT)
                else:
                    credential = credentials.ApplicationDefault()
                app = firebase_admin.initialize_app(
                    credential, {"projectId": config.FIREBASE_PROJECT_ID}, name="deltavox-inbox"
                )
            _firebase_app = app
            _firestore_client = firestore.client(app=app)
        except Exception as exc:
            raise InboxUnavailable("The Firestore inbox could not be initialized.") from exc
    return _firebase_app, _firestore_client


def save_request(*, name: str, email: str, category: str, message: str) -> str:
    _, db = _clients()
    try:
        from firebase_admin import firestore

        ref = db.collection(COLLECTION).document()
        ref.set({
            "name": name.strip()[:60],
            "email": email.strip().lower()[:254],
            "category": category,
            "message": message.strip()[:1000],
            "status": "new",
            "createdAt": firestore.SERVER_TIMESTAMP,
        })
        return ref.id
    except Exception as exc:
        raise InboxUnavailable("The message could not be saved to the developer inbox.") from exc

import hashlib
import secrets
from typing import Optional, Tuple

from models import User, UserApiToken
from extensions import db


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token_for_user(
    user: User,
    name: Optional[str] = None,
    scopes: str = "steps:write",
) -> Tuple[UserApiToken, str]:
    while True:
        plaintext = generate_token()
        token_hash = hash_token(plaintext)
        existing = UserApiToken.query.filter_by(token_hash=token_hash).first()
        if not existing:
            break

    token = UserApiToken(
        user_id=user.id,
        name=name.strip() if name else None,
        token_hash=token_hash,
        scopes=scopes or "steps:write",
    )
    db.session.add(token)
    db.session.commit()
    return token, plaintext

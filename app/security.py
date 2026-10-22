import base64
import hashlib
import hmac
import secrets

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES = 16
KEY_BYTES = 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=KEY_BYTES,
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(key).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=SCRYPT_MAXMEM,
            dklen=KEY_BYTES,
        )
        return hmac.compare_digest(key, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)

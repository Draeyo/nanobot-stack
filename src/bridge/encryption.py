"""encryption.py — Field-level AES-256-GCM encryption for nanobot-stack (Sub-projet L).

Provides FieldEncryptor for encrypting/decrypting individual TEXT values stored in
SQLite or Qdrant payloads. Uses HKDF-SHA256 for domain-isolated key derivation.

Encrypted format:  enc:v1:<base64url_no_padding(nonce[12] + ciphertext + tag[16])>

Usage:
    enc = FieldEncryptor(os.environ["ENCRYPTION_MASTER_KEY"], "sqlite-v1")
    stored = enc.encrypt_field(plaintext)
    plaintext = enc.decrypt_field(stored)
"""
from __future__ import annotations

import base64
import logging
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger("rag-bridge.encryption")

NONCE_SIZE = 12  # bytes — 96-bit nonce, NIST recommended for GCM


class DecryptionError(Exception):
    """Raised when AES-GCM decryption fails (tampered data or wrong key)."""


class FieldEncryptor:
    """AES-256-GCM field encryptor with HKDF-derived domain keys.

    Args:
        master_key_hex: 64-character hexadecimal string (256-bit master key).
        context: HKDF info label — "sqlite-v1" or "qdrant-v1".
    """

    PREFIX = "enc:v1:"

    def __init__(self, master_key_hex: str, context: str) -> None:
        if len(master_key_hex) != 64:
            raise ValueError(
                f"ENCRYPTION_MASTER_KEY must be exactly 64 hex characters (got {len(master_key_hex)})"
            )
        master_key_bytes = bytes.fromhex(master_key_hex)  # raises ValueError on non-hex
        self._derived_key: bytes = self._derive_key(master_key_bytes, context)
        self._context = context

    @staticmethod
    def _derive_key(master_key_bytes: bytes, context_label: str) -> bytes:
        """Derive a 32-byte AES key from the master key using HKDF-SHA256."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=context_label.encode("utf-8"),
        )
        return hkdf.derive(master_key_bytes)

    def encrypt_field(self, value: str) -> str:
        """Encrypt value with AES-256-GCM. Returns enc:v1:<base64url> string.

        Each call generates a fresh random 12-byte nonce — two encryptions of
        the same plaintext produce different ciphertexts.
        """
        nonce = secrets.token_bytes(NONCE_SIZE)
        aesgcm = AESGCM(self._derived_key)
        ciphertext_with_tag = aesgcm.encrypt(nonce, value.encode("utf-8"), None)
        blob = nonce + ciphertext_with_tag  # nonce(12) + ciphertext + tag(16)
        b64 = base64.urlsafe_b64encode(blob).rstrip(b"=").decode("ascii")
        return f"{self.PREFIX}{b64}"

    def decrypt_field(self, value: str) -> str:
        """Decrypt an enc:v1: prefixed value. Returns plaintext unchanged if not prefixed.

        Raises DecryptionError if the prefix is present but decryption fails
        (tampered data, wrong key, or truncated blob).
        """
        if not value.startswith(self.PREFIX):
            return value  # plaintext passthrough — backward compatibility
        b64 = value[len(self.PREFIX):]
        # Restore base64 padding
        padding = (4 - len(b64) % 4) % 4
        b64_padded = b64 + "=" * padding
        try:
            blob = base64.urlsafe_b64decode(b64_padded)
            if len(blob) < NONCE_SIZE + 16:
                raise ValueError("blob too short")
            nonce = blob[:NONCE_SIZE]
            ciphertext_with_tag = blob[NONCE_SIZE:]
            aesgcm = AESGCM(self._derived_key)
            plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
            return plaintext_bytes.decode("utf-8")
        except Exception as exc:
            raise DecryptionError(
                f"Failed to decrypt field (context={self._context}): {exc}"
            ) from exc

    def is_encrypted(self, value: str) -> bool:
        """Return True if value starts with the enc:v1: prefix."""
        return value.startswith(self.PREFIX)


def validate_encryption_key(encryptor: FieldEncryptor | None) -> None:
    """Validate that the encryptor can round-trip a sentinel value.

    Called at app startup when ENCRYPTION_ENABLED=true. Raises RuntimeError
    and blocks startup if the key is absent or invalid.
    """
    if encryptor is None:
        raise RuntimeError(
            "ENCRYPTION_MASTER_KEY is required when ENCRYPTION_ENABLED=true. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    sentinel = "nanobot-encryption-sentinel-v1"
    encrypted = encryptor.encrypt_field(sentinel)
    decrypted = encryptor.decrypt_field(encrypted)
    if decrypted != sentinel:
        raise RuntimeError(
            "ENCRYPTION_MASTER_KEY invalid: encrypt/decrypt round-trip test failed. "
            "Check the ENCRYPTION_MASTER_KEY environment variable."
        )
    logger.info("Encryption key validation passed (context=%s)", encryptor._context)  # pylint: disable=protected-access

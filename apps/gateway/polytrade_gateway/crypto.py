"""Authenticated encryption for custodial API credentials and alert targets."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class CredentialCipher:
    def __init__(self, key: bytes, key_version: str = "v1") -> None:
        if len(key) != 32:
            raise ValueError("Credential encryption key must be 32 bytes")
        self._cipher = AESGCM(key)
        self._key_version = key_version

    def encrypt(self, value: Any, associated_data: str) -> str:
        nonce = os.urandom(12)
        encrypted = self._cipher.encrypt(
            nonce,
            json.dumps(value, separators=(",", ":")).encode(),
            associated_data.encode(),
        )
        ciphertext, tag = encrypted[:-16], encrypted[-16:]
        return ".".join((self._key_version, _encode(nonce), _encode(tag), _encode(ciphertext)))

    def decrypt(self, envelope: str, associated_data: str) -> Any:
        parts = envelope.split(".")
        if len(parts) != 4 or parts[0] != self._key_version:
            raise ValueError("Unsupported credential envelope")
        _, nonce, tag, ciphertext = parts
        plaintext = self._cipher.decrypt(
            _decode(nonce),
            _decode(ciphertext) + _decode(tag),
            associated_data.encode(),
        )
        return json.loads(plaintext)

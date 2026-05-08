"""
Polymarket CLOB L2 request signer.

Polymarket uses two auth levels:
  L1 — EIP-712 signature with a private key to create/derive API credentials.
       Headers: POLY_ADDRESS, POLY_SIGNATURE, POLY_TIMESTAMP, POLY_NONCE
  L2 — HMAC-SHA256 with the API secret derived from L1, used for all trading requests.
       Headers: POLY_ADDRESS, POLY_SIGNATURE, POLY_TIMESTAMP, POLY_API_KEY, POLY_PASSPHRASE

This module implements L2 only. API credentials (key, secret, passphrase) must be
generated externally via L1 (see Polymarket docs / py-clob-client for credential creation).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Dict


class PolymarketSigner:
    """
    L2 HMAC-SHA256 signer for Polymarket CLOB authenticated endpoints.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        address: str,   # wallet address (0x...)
    ) -> None:
        self.api_key = api_key
        self._api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.address = address

    def headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """
        Return L2 auth headers for a request.

        Signature message: timestamp + METHOD + path + body
        Signature: base64( HMAC-SHA256(secret, message) )
        """
        ts = str(int(time.time()))
        message = ts + method.upper() + path + body
        sig = base64.b64encode(
            hmac.new(
                self._api_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
        ).decode()

        return {
            "POLY_ADDRESS": self.address,
            "POLY_SIGNATURE": sig,
            "POLY_TIMESTAMP": ts,
            "POLY_API_KEY": self.api_key,
            "POLY_PASSPHRASE": self.api_passphrase,
        }

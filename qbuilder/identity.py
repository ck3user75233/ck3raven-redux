"""Opaque content-version identity generation owned by QBuilder."""

from __future__ import annotations

import secrets

_CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CVID_GROUPS = 2
_CVID_GROUP_LENGTH = 4


def new_cvid() -> str:
    """Return a random, non-semantic content-version identifier."""
    groups = [
        "".join(secrets.choice(_CROCKFORD_BASE32) for _ in range(_CVID_GROUP_LENGTH))
        for _ in range(_CVID_GROUPS)
    ]
    return f"CVID-{'-'.join(groups)}"

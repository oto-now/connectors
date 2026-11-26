"""Le logo d'un connecteur : `connectors/<nom>/logo.png`, facultatif, à côté de sa description.

Un écran d'hôte montre ce logo sur la tuile de l'outil sans lire aucun site tiers : la fabrique le porte, encodé en
base64 sans préfixe `data:`, dans la définition TypeScript (`logo`) et dans la description servie en Python
(`connector.logo`). Le fichier est un PNG carré de 64 × 64 pixels, de 16 Ko au plus ; un fichier qui n'en est pas un
fait échouer la description, et rien n'est généré. Seuls la signature et l'en-tête `IHDR` sont lus : la fabrique ne
décode pas l'image.
"""
from __future__ import annotations

import base64
import pathlib
import struct

NAME = "logo.png"
SIZE = 64
MAX_BYTES = 16 * 1024
SIGNATURE = b"\x89PNG\r\n\x1a\n"


class LogoError(ValueError):
    """Un `logo.png` que le format refuse ; le message dit pourquoi."""


def logo_of(folder: pathlib.Path) -> str | None:
    """Le `logo.png` du dossier d'un connecteur, en base64 ; `None` sans fichier ; `LogoError` s'il est refusé."""
    path = folder / NAME
    if not path.is_file():
        return None
    data = path.read_bytes()
    return base64.b64encode(checked(data)).decode("ascii")


def checked(data: bytes) -> bytes:
    """Les octets d'un logo admis : PNG (signature, puis `IHDR` en premier bloc), 64 × 64, 16 Ko au plus."""
    if len(data) > MAX_BYTES:
        raise LogoError(f"{NAME}: {len(data)} bytes, at most {MAX_BYTES}")
    if len(data) < 24 or not data.startswith(SIGNATURE) or data[12:16] != b"IHDR":
        raise LogoError(f"{NAME}: not a PNG file")
    width, height = struct.unpack(">II", data[16:24])
    if (width, height) != (SIZE, SIZE):
        raise LogoError(f"{NAME}: {width}x{height}, expected a square {SIZE}x{SIZE}")
    return data

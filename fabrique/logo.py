"""Le logo d'un connecteur : `connectors/<nom>/logo.png`, facultatif, à côté de sa description.

Un écran d'hôte montre ce logo sur la tuile de l'outil sans lire aucun site tiers : la fabrique le porte, encodé en
base64 sans préfixe `data:`, dans la définition TypeScript (`logo`) et dans la description servie en Python
(`connector.logo`). Le fichier est un PNG carré de 64 × 64 pixels, de 16 Ko au plus ; un fichier qui n'en est pas un
fait échouer la description, et rien n'est généré. La fabrique ne décode pas l'image, mais elle parcourt ses blocs :
longueur et CRC de chacun, `IHDR` en premier (13 octets), `IEND` en dernier et rien après, et seuls les blocs d'une
image (`ALLOWED`) ; un bloc de texte ou EXIF est refusé. Ainsi aucun octet que personne ne relit ne passe en base64
dans les sorties publiées.
"""
from __future__ import annotations

import base64
import pathlib
import struct
import zlib

NAME = "logo.png"
SIZE = 64
MAX_BYTES = 16 * 1024
SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Les blocs d'une image et de sa couleur ; les blocs de texte (`tEXt`, `iTXt`, `zTXt`) et EXIF (`eXIf`) n'en sont pas.
ALLOWED = frozenset({b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"iCCP", b"sRGB", b"gAMA", b"pHYs"})


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
    """Les octets d'un logo admis : PNG aux blocs sains (`chunks`), 64 × 64, 16 Ko au plus."""
    if len(data) > MAX_BYTES:
        raise LogoError(f"{NAME}: {len(data)} bytes, at most {MAX_BYTES}")
    if not data.startswith(SIGNATURE):
        raise LogoError(f"{NAME}: not a PNG file")
    found = chunks(data)
    kind, body = found[0]
    if kind != b"IHDR" or len(body) != 13:
        raise LogoError(f"{NAME}: not a PNG file (IHDR of 13 bytes must come first)")
    width, height = struct.unpack(">II", body[:8])
    if (width, height) != (SIZE, SIZE):
        raise LogoError(f"{NAME}: {width}x{height}, expected a square {SIZE}x{SIZE}")
    return data


def chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """Les blocs `(type, contenu)` d'un PNG après sa signature : longueur et CRC contrôlés, types de `ALLOWED`,
    `IEND` en dernier et aucun octet après ; `LogoError` sinon."""
    found: list[tuple[bytes, bytes]] = []
    at = len(SIGNATURE)
    while at < len(data):
        if at + 12 > len(data):
            raise LogoError(f"{NAME}: not a PNG file (truncated chunk)")
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        end = at + 12 + length
        if end > len(data):
            raise LogoError(f"{NAME}: not a PNG file (truncated chunk)")
        body = data[at + 8:at + 8 + length]
        (crc,) = struct.unpack(">I", data[at + 8 + length:end])
        if zlib.crc32(kind + body) != crc:
            raise LogoError(f"{NAME}: not a PNG file (bad CRC in {kind.decode('latin-1')})")
        if kind not in ALLOWED:
            raise LogoError(f"{NAME}: chunk {kind.decode('latin-1')} not allowed (only {', '.join(sorted(k.decode() for k in ALLOWED))})")
        found.append((kind, body))
        at = end
        if kind == b"IEND":
            if at != len(data):
                raise LogoError(f"{NAME}: {len(data) - at} bytes after IEND")
            return found
    raise LogoError(f"{NAME}: not a PNG file (no IEND chunk)")

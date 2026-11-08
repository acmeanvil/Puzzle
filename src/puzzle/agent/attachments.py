"""Files sent to the model with a message (ADR 0074): chosen, dropped or pasted.

Images go as images, PDFs as documents where the provider reads them, anything else as
its text. Kept in the chat's messages, never in the project.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".gif": "image/gif", ".webp": "image/webp"}  # fmt: skip
MAX_TEXT = 200_000  # characters of a text file sent; the rest is cut, with a note
MAX_BYTES = 20 * 2**20  # an image or PDF larger than this is refused


class AttachmentError(ValueError):
    pass


@dataclass(frozen=True)
class Attachment:
    name: str
    kind: str  # "image", "pdf" or "text"
    media_type: str
    data: bytes = b""  # an image's or PDF's bytes
    text: str = ""  # a text file's text

    @property
    def is_file(self) -> bool:
        """Sent as a file (an image or a PDF), not as text: held back from a remote
        provider unless the user lets files go (ADR 0015's send_images)."""
        return self.kind in ("image", "pdf")

    def label(self) -> str:
        if self.kind == "text":
            return f"{self.name} ({len(self.text):,} characters)"
        return f"{self.name} ({len(self.data) / 1024:,.0f} kB)"

    def block(self) -> dict:
        """The JSON form kept in the chat's messages."""
        out = {"name": self.name, "kind": self.kind, "media_type": self.media_type}
        if self.kind == "text":
            out["text"] = self.text
        else:
            out["data"] = base64.b64encode(self.data).decode("ascii")
        return out


def from_path(path: Path | str) -> Attachment:
    """A file as an attachment: an image, a PDF, or its text (cut at MAX_TEXT)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in IMAGES or suffix == ".pdf":
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            raise AttachmentError(f"{path.name} is {len(data) / 2**20:.0f} MB; at most "
                                  f"{MAX_BYTES // 2**20} MB can be attached")  # fmt: skip
        if suffix == ".pdf":
            return Attachment(path.name, "pdf", "application/pdf", data=data)
        return Attachment(path.name, "image", IMAGES[suffix], data=data)
    raw = path.read_bytes()
    if b"\0" in raw[:4096]:
        raise AttachmentError(f"{path.name} is not text, an image or a PDF")
    return from_text(raw.decode("utf-8", errors="replace"), path.name)


def from_text(text: str, name: str = "pasted.txt") -> Attachment:
    if len(text) > MAX_TEXT:
        cut = len(text) - MAX_TEXT
        text = text[:MAX_TEXT] + f"\n... ({cut:,} more characters not sent)"
    return Attachment(name, "text", "text/plain", text=text)


def from_image(png: bytes, name: str = "pasted.png") -> Attachment:
    return Attachment(name, "image", "image/png", data=png)


def text_of(block: dict) -> str:
    """A text attachment as the model reads it, named."""
    return f'<file name="{block["name"]}">\n{block["text"]}\n</file>'

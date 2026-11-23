"""Fail-closed ClamAV INSTREAM integration; contents never become executable input."""

import hashlib
import socket
import struct

from mercury.errors import DomainError, require
from mercury.models import Attachment
from mercury.services import audit, owned


def scan_bytes(settings, content):
    require(settings.scanner_mode == "clamav", "scanner_unconfigured", "附件扫描服务未配置", 503)
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port), timeout=settings.scanner_timeout
        ) as connection:
            connection.settimeout(settings.scanner_timeout)
            connection.sendall(b"zINSTREAM\0")
            for offset in range(0, len(content), 65536):
                chunk = content[offset : offset + 65536]
                connection.sendall(struct.pack("!I", len(chunk)) + chunk)
            connection.sendall(struct.pack("!I", 0))
            response = b""
            while b"\0" not in response and len(response) <= 4096:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response += chunk
            result = response.rstrip(b"\0").decode("utf-8", errors="replace")
            if result == "stream: OK":
                return "clean"
            if result.startswith("stream: ") and result.endswith(" FOUND"):
                return "infected"
            raise DomainError("scanner_failed", "扫描未得到可信结果，附件继续隔离", 503)
    except OSError:
        raise DomainError("scanner_unavailable", "扫描服务不可用，附件继续隔离", 503) from None


def read_attachment(db, item):
    path = db.settings.data_dir / "uploads" / item.id
    require(path.is_file() and not path.is_symlink(), "attachment_unavailable", "附件不可用", 404)
    content = path.read_bytes()
    require(
        len(content) == item.size and hashlib.sha256(content).hexdigest() == item.sha256,
        "attachment_integrity",
        "附件完整性校验失败",
        423,
    )
    return content


def scan_attachment(db, ctx, attachment_id):
    with db.session(ctx) as s:
        item = owned(s, Attachment, ctx, attachment_id)
        if item.scan_status in {"clean", "infected"}:
            return
        content = read_attachment(db, item)
    status = scan_bytes(db.settings, content)
    with db.session(ctx, write=True) as s:
        item = owned(s, Attachment, ctx, attachment_id, True)
        item.scan_status = status
        audit(s, ctx, "attachment.scanned", item.case_id, {"attachment_id": item.id, "status": status})

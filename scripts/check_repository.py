"""Check the staged/tracked publication set without printing candidate secret values."""

import re
import subprocess
from pathlib import Path

result = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True)
paths = [Path(name.decode()) for name in result.stdout.split(b"\x00") if name]
patterns = [
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
failures = []
for path in paths:
    if path.name == ".env" or any(
        part in {".data", ".venv", "node_modules", "__pycache__"} for part in path.parts
    ):
        failures.append(f"forbidden generated/local path: {path.as_posix()}")
    if not path.is_file():
        continue
    data = path.read_bytes()
    if len(data) > 20 * 1024 * 1024:
        failures.append(f"unexpected large tracked file: {path.as_posix()}")
    if any(pattern.search(data) for pattern in patterns):
        failures.append(f"possible credential material: {path.as_posix()}")
if failures:
    raise SystemExit("\n".join(failures))
print(f"Publication checks passed for {len(paths)} tracked files; no credential values emitted.")

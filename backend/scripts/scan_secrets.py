"""Scan files for credentials. Used by CI on tracked files and the built frontend bundle.

    python -m scripts.scan_secrets --git                 # every git-tracked file in the repository
    python -m scripts.scan_secrets PATH [PATH ...]       # files or directories (e.g. frontend/dist)

Reports file and pattern name only; never prints the matched value. Exit code 1 if anything matches.
Patterns are deliberately specific (provider key prefixes, private keys, credentials in URLs) to keep
false positives low; `.env.example` placeholders don't match because they are empty.
"""

import argparse
from collections.abc import Iterable, Iterator
from pathlib import Path
import re
import subprocess
import sys

PATTERNS = {
    "anthropic_key": re.compile(rb"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai_style_key": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"),
    "aws_access_key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "credential_in_url": re.compile(rb"\b(?:postgres(?:ql)?|redis|rediss|mysql|amqp|https?)://[^\s:/@'\"]+:[^\s@/'\"$<{]{8,}@"),
    "assigned_secret": re.compile(rb"(?i)\b(?:LLM_API_KEY|ANTHROPIC_API_KEY|OPENAI_API_KEY|ADMIN_API_TOKENS)[ \t]*[=:][ \t]*['\"]?[A-Za-z0-9_\-]{16,}"),
}
MAX_BYTES = 5 * 2**20
# Test fixtures use obviously fake values on purpose; they're still scanned, but these literals are allowed.
ALLOWED_VALUES = [b"redis-password-123", b"db-password-456"]


ALLOW_MARKER = b"scan-secrets: allow"  # put on the same line as a deliberately fake test credential


def scan_bytes(data: bytes) -> list[str]:
    for allowed in ALLOWED_VALUES:
        data = data.replace(allowed, b"")
    hits = []
    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(data):
            start = data.rfind(b"\n", 0, match.start()) + 1
            end = data.find(b"\n", match.end())
            if ALLOW_MARKER not in data[start : end if end != -1 else len(data)]:
                hits.append(name)
                break
    return hits


def iter_paths(paths: Iterable[str]) -> Iterator[tuple[str, bytes]]:
    for raw in paths:
        path = Path(raw)
        if not path.exists():  # e.g. a file deleted in the change being scanned
            continue
        files = [p for p in path.rglob("*") if p.is_file()] if path.is_dir() else [path]
        for f in files:
            if f.stat().st_size <= MAX_BYTES:
                yield str(f), f.read_bytes()


def iter_git() -> Iterator[tuple[str, bytes]]:
    names = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0")  # noqa: S603, S607 - fixed argv
    yield from iter_paths(n.decode() for n in names if n and Path(n.decode()).is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--git", action="store_true")
    args = parser.parse_args()
    sources: list[Iterator[tuple[str, bytes]]] = []
    if args.git:
        sources.append(iter_git())
    if args.paths:
        sources.append(iter_paths(args.paths))
    scanned, findings = 0, []
    for source in sources:
        for name, data in source:
            scanned += 1
            findings += [(name, hit) for hit in scan_bytes(data)]
    for name, hit in findings:
        print(f"SECRET? {hit}: {name}")
    print(f"scanned {scanned} files, {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

"""Quét secret và PII trong các file git đang track (hoặc đã stage) trước khi push.

    python scripts/scan_repo.py            # quét file được git track
    python scripts/scan_repo.py --staged   # chỉ quét file đang stage (dùng cho pre-commit)

Thoát với mã 1 nếu phát hiện vấn đề. Dữ liệu PII *giả* dùng làm fixture/evidence
được liệt kê rõ trong PII_ALLOWLIST; secret thì không có allowlist.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio
from app.pii import PII_PATTERNS

SECRET_PATTERNS = {
    "langfuse_secret_key": re.compile(r"sk-lf-[0-9a-f]{8}-[0-9a-f-]{20,}"),
    "langfuse_public_key": re.compile(r"pk-lf-[0-9a-f]{8}-[0-9a-f-]{20,}"),
    "openai_or_anthropic_key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
FORBIDDEN_FILES = {".env", "config/challenge.json", "data/logs.jsonl", "data/audit.jsonl"}
# File chứa PII giả có chủ đích: fixture test, input mẫu của đề, evidence redaction, tài liệu.
PII_ALLOWLIST = ("tests/", "data/sample_queries.jsonl", "submission/evidence/05-pii-redaction.txt", "docs/")
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".pyc"}
PII_REGEXES = {name: re.compile(p) for name, p in PII_PATTERNS.items()}


def list_files(staged: bool) -> list[str]:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"] if staged else ["git", "ls-files"]
    out = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def scan(files: list[str]) -> list[str]:
    problems = []
    for rel in files:
        if rel in FORBIDDEN_FILES:
            problems.append(f"{rel}: file không được commit")
            continue
        path = REPO_ROOT / rel
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    problems.append(f"{rel}:{lineno}: secret ({name})")
            if rel.startswith(PII_ALLOWLIST):
                continue
            for name, pattern in PII_REGEXES.items():
                if pattern.search(line):
                    problems.append(f"{rel}:{lineno}: PII ({name})")
    return problems


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    files = list_files(args.staged)
    problems = scan(files)
    for problem in problems:
        print(f"[FAIL] {problem}")
    print(f"Đã quét {len(files)} file: {'KHÔNG ĐẠT' if problems else 'SẠCH'} ({len(problems)} vấn đề)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

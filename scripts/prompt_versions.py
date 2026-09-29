"""Quản lý prompt `day13-chat` trong project Langfuse cá nhân.

    python scripts/prompt_versions.py setup            # tạo v1 (baseline, production) và v2 (candidate)
    python scripts/prompt_versions.py show             # in version nào đang giữ label nào
    python scripts/prompt_versions.py promote 2        # gắn label production cho version 2
    python scripts/prompt_versions.py rollback 1       # đưa production về version 1

Langfuse chỉ cho một version giữ một label, nên gắn `production` cho version mới
đồng nghĩa với gỡ label đó khỏi version cũ. App cache prompt 60 giây
(`cache_ttl_seconds=60`), nên sau khi đổi label cần chờ ~60 giây hoặc restart API.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio

PROMPT_V1 = "Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}"
PROMPT_V2 = (
    "Feature={{feature}}\n"
    "Docs={{docs}}\n"
    "Question={{message}}\n"
    "Answer in at most 3 short bullet points and only use the Docs above."
)


def _client():
    from langfuse import get_client

    client = get_client()
    if not client.auth_check():
        raise SystemExit("Không xác thực được Langfuse; kiểm tra key trong .env")
    return client


def _versions(client, name: str) -> list[dict]:
    rows = []
    version = 1
    while True:
        try:
            prompt = client.get_prompt(name, version=version, cache_ttl_seconds=0, max_retries=0)
        except Exception:
            break
        rows.append({"version": prompt.version, "labels": list(prompt.labels)})
        version += 1
    return rows


def show(client, name: str) -> None:
    rows = _versions(client, name)
    if not rows:
        print(f"Prompt '{name}' chưa có version nào.")
        return
    for row in rows:
        print(f"{name} v{row['version']}: labels={row['labels']}")


def setup(client, name: str) -> None:
    if _versions(client, name):
        print(f"Prompt '{name}' đã tồn tại, không tạo lại.")
        show(client, name)
        return
    client.create_prompt(
        name=name,
        prompt=PROMPT_V1,
        labels=["baseline", "production"],
        type="text",
        commit_message="v1: baseline template (Feature/Docs/Question)",
    )
    client.create_prompt(
        name=name,
        prompt=PROMPT_V2,
        labels=["candidate"],
        type="text",
        commit_message="v2: giới hạn câu trả lời 3 bullet, chỉ dùng Docs",
    )
    show(client, name)


def move_production(client, name: str, version: int) -> None:
    client.update_prompt(name=name, version=version, new_labels=["production"])
    show(client, name)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["setup", "show", "promote", "rollback"])
    parser.add_argument("version", nargs="?", type=int)
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    name = os.getenv("LANGFUSE_PROMPT_NAME", "day13-chat")
    client = _client()
    if args.action == "setup":
        setup(client, name)
    elif args.action == "show":
        show(client, name)
    else:
        if args.version is None:
            parser.error(f"{args.action} cần số version, ví dụ: {args.action} 2")
        move_production(client, name, args.version)
    client.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

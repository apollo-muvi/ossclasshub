#!/usr/bin/env python3
"""ClassHub OSS — 一步設定 .env

在 Linux / macOS / Windows 都能跑。
做的事：把 .env.example 複製到 api/.env，然後自動產生安全的 CLASSHUB_SECRET。

用法：
    python3 setup-env.py          # 互動模式
    python3 setup-env.py --force  # 覆蓋已有的 .env
"""
import secrets
import sys
from pathlib import Path


def main():
    repo_root = Path(__file__).resolve().parent
    example = repo_root / ".env.example"
    target = repo_root / "api" / ".env"
    force = "--force" in sys.argv

    # --- 檢查 .env.example 是否存在 ---
    if not example.exists():
        print(f"錯誤：找不到 {example.name}，請確認你在 repo 根目錄下執行。")
        sys.exit(1)

    # --- 檢查 .env 是否已存在 ---
    if target.exists() and not force:
        print(f"已經有 {target} 了。")
        answer = input("要覆蓋嗎？（輸入 y 覆蓋，其他取消）: ").strip().lower()
        if answer != "y":
            print("取消，不做任何變更。")
            return
        force = True

    # --- 產生隨機 SECRET ---
    new_secret = secrets.token_hex(32)

    # --- 讀取範例，替換 SECRET ---
    content = example.read_text(encoding="utf-8")
    lines = content.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("CLASSHUB_SECRET="):
            lines[i] = f"CLASSHUB_SECRET={new_secret}"
            replaced = True
            break

    if not replaced:
        # .env.example 裡找不到那行，直接附加
        lines.append(f"CLASSHUB_SECRET={new_secret}")

    # --- 寫入 api/.env ---
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    action = "覆蓋" if force else "建立"
    print(f"\n完成！已{action} {target}")
    print(f"CLASSHUB_SECRET 已自動產生（64 字 hex）")
    print(f"\n接下來：")
    print(f"  cd api")
    print(f"  python3 -m venv .venv && . .venv/bin/activate   # Windows: .venv\\Scripts\\activate")
    print(f"  pip install -e \".[dev]\"")
    print(f"  python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8100")


if __name__ == "__main__":
    main()

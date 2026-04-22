#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

def main():
    if len(sys.argv) != 2:
        print("Использование: python count_braces.py <путь_к_папке>")
        sys.exit(1)

    folder = Path(sys.argv[1])
    if not folder.is_dir():
        print(f"Ошибка: '{folder}' не является папкой.")
        sys.exit(1)

    total_braces = 0
    symbol = '{'

    for item in folder.iterdir():
        if item.is_file():
            try:
                content = item.read_text(encoding='utf-8')
                count = content.count(symbol)
                if count > 0:
                    print(f"{item.name}: {count}")
                    total_braces += count
            except Exception as e:
                print(f"Ошибка чтения {item.name}: {e}", file=sys.stderr)

    print(f"\nВсего символов '{symbol}': {total_braces}")

if __name__ == "__main__":
    main()
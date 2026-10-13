"""List every symbol your MT5 account offers, grouped by category. Read-only.

    python list_symbols.py            # prints a summary, saves full list to output/symbols.txt
"""
import os
from collections import defaultdict
import data


def main():
    mt5 = data.mt5_connect()
    try:
        symbols = mt5.symbols_get() or []
    finally:
        mt5.shutdown()
    groups = defaultdict(list)
    for s in symbols:
        path = getattr(s, "path", "") or "Other"
        group = path.replace("/", "\\").rsplit("\\", 1)[0] if "\\" in path or "/" in path else "Other"
        groups[group].append((s.name, getattr(s, "description", "")))

    os.makedirs("output", exist_ok=True)
    with open(os.path.join("output", "symbols.txt"), "w", encoding="utf-8") as f:
        for g in sorted(groups):
            f.write(f"\n[{g}]  ({len(groups[g])})\n")
            for name, desc in sorted(groups[g]):
                f.write(f"  {name:20s} {desc}\n")

    print(f"{len(symbols)} symbols in {len(groups)} categories:\n")
    for g in sorted(groups):
        names = sorted(n for n, _ in groups[g])
        shown = ", ".join(names[:25]) + (f"  … +{len(names) - 25} more" if len(names) > 25 else "")
        print(f"[{g}] ({len(names)})\n  {shown}\n")
    print("Full list with descriptions: output\\symbols.txt")


if __name__ == "__main__":
    main()

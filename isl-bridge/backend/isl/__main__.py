"""Try it from the terminal:   python -m isl "What is your name?"   (run inside backend/)"""
import json
import sys

from .processor import ISLProcessor


def main():
    text = " ".join(a for a in sys.argv[1:] if a != "--json").strip()
    if not text:
        print('Usage: python -m isl "your English sentence"')
        sys.exit(1)
    result = ISLProcessor().process(text)
    print("GLOSS:", result["gloss_string"])
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for s in result["sentences"]:
            a = s["analysis"]
            print(f"  [{a['type']}] {s['english']}")
        if result["dropped"]:
            print("  removed:", ", ".join(f"{d['word']} ({d['reason']})" for d in result["dropped"]))


if __name__ == "__main__":
    main()

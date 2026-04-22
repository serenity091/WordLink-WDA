#!/usr/bin/env python3
"""Build the bot dictionary from SCOWL word lists."""

from __future__ import annotations

import tarfile
import urllib.request
from pathlib import Path

from wordfreq import zipf_frequency


SCOWL_VERSION = "2020.12.07"
SCOWL_URL = (
    "https://downloads.sourceforge.net/project/wordlist/"
    f"SCOWL/{SCOWL_VERSION}/scowl-{SCOWL_VERSION}.tar.gz"
)
SCOWL_MAX_SIZE = 60
SCOWL_SPELLINGS = ("english", "american")
MIN_WORD_LENGTH = 3
MAX_WORD_LENGTH = 16
USE_WORDFREQ_FILTER = True
MIN_ZIPF_FREQUENCY = 2.75
CACHE_PATH = Path("data/.cache") / f"scowl-{SCOWL_VERSION}.tar.gz"
OUTPUT_PATH = Path("data/scowl_words.txt")


def main() -> int:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CACHE_PATH.exists():
        print(f"downloading {SCOWL_URL}")
        urllib.request.urlretrieve(SCOWL_URL, CACHE_PATH)

    words = load_scowl_words(CACHE_PATH)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8")
    print(f"wrote {len(words)} words to {OUTPUT_PATH}")
    return 0


def load_scowl_words(archive_path: Path) -> set[str]:
    words: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if not member.isfile() or len(path.parts) < 3 or path.parts[-2] != "final":
                continue
            if not is_scowl_word_file(path.name):
                continue

            extracted = archive.extractfile(member)
            if extracted is None:
                continue

            for raw_line in extracted.read().decode("iso-8859-1").splitlines():
                word = normalize_word(raw_line)
                if is_usable_word(word):
                    words.add(word)

    return words


def is_scowl_word_file(filename: str) -> bool:
    stem, _, size_text = filename.rpartition(".")
    if not size_text.isdigit() or int(size_text) > SCOWL_MAX_SIZE:
        return False
    return any(stem == f"{spelling}-words" for spelling in SCOWL_SPELLINGS)


def normalize_word(value: str) -> str:
    return value.strip().lower()


def is_usable_word(word: str) -> bool:
    if not (MIN_WORD_LENGTH <= len(word) <= MAX_WORD_LENGTH and word.isalpha()):
        return False
    if USE_WORDFREQ_FILTER and zipf_frequency(word, "en") < MIN_ZIPF_FREQUENCY:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())

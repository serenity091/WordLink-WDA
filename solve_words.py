#!/usr/bin/env python3
"""Solve a 4x4 letter board and rank words by length, then dot score."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import sys


def runtime_root() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent


root = runtime_root()
LETTERS_PATH = Path("latest_letters.json")
DOTS_PATH = Path("latest_dots.json")
BEST_WORD_PATH = Path("latest_best_word.json")
SCOWL_DICTIONARY_PATH = root / "data/scowl_words.txt"
MIN_WORD_LENGTH = 3
MAX_WORD_LENGTH = 16
BLOCKED_WORDS = {
    "bergut",
    "conte",
    "dak",
    "daw",
    "denter",
    "dhoni",
    "dunn",
    "enoch",
    "greund",
    "henter",
    "hogget",
    "idk",
    "intel",
    "ira",
    "karin",
    "monique",
    "mohur",
    "monte",
    "rudy",
    "wyatt",
    "wynn",
    "yun",
    "yuri",
    "aquino",
}


@dataclass(frozen=True)
class WordResult:
    word: str
    dot_score: int
    path: list[tuple[int, int]]

    @property
    def length(self) -> int:
        return len(self.word)

    def as_dict(self) -> dict:
        return {
            "word": self.word,
            "length": self.length,
            "dot_score": self.dot_score,
            "path": self.path,
        }


class TrieNode(dict):
    terminal_word: str | None = None


def main() -> int:
    letters = json.loads(LETTERS_PATH.read_text())
    dots = json.loads(DOTS_PATH.read_text())
    results = solve_board(letters, dots)
    BEST_WORD_PATH.write_text(json.dumps(results[0].as_dict() if results else None, indent=2))

    if results:
        best = results[0]
        print(f"best: {best.word} length={best.length} dots={best.dot_score} path={best.path}")
    return 0


def solve_board(letters: list[list[str]], dots: list[list[int]], result_limit: int | None = None) -> list[WordResult]:
    validate_board(letters, dots)
    results = solve_board_with_trie(letters, dots, dictionary_trie())
    return results if result_limit is None else results[:result_limit]


def solve_board_with_trie(letters: list[list[str]], dots: list[list[int]], trie: TrieNode) -> list[WordResult]:
    tile_texts = tuple(normalize_tile_text(letters[row][col]) for row in range(4) for col in range(4))
    dot_scores = tuple(int(dots[row][col]) for row in range(4) for col in range(4))
    found: dict[str, WordResult] = {}

    for position in range(16):
        walk(
            position=position,
            node=trie,
            used_mask=0,
            char_count=0,
            current_score=0,
            current_path=[],
            tile_texts=tile_texts,
            dot_scores=dot_scores,
            found=found,
        )

    return sorted(found.values(), key=lambda item: (-item.length, -item.dot_score, item.word))


def walk(
    position: int,
    node: TrieNode,
    used_mask: int,
    char_count: int,
    current_score: int,
    current_path: list[int],
    tile_texts: tuple[str, ...],
    dot_scores: tuple[int, ...],
    found: dict[str, WordResult],
) -> None:
    position_bit = 1 << position
    if used_mask & position_bit:
        return

    tile_text = tile_texts[position]
    next_char_count = char_count + len(tile_text)
    if next_char_count > MAX_WORD_LENGTH:
        return

    next_node = consume_tile(node, tile_text)
    if next_node is None:
        return

    next_score = current_score + dot_scores[position]
    current_path.append(position)

    if next_node.terminal_word and next_char_count >= MIN_WORD_LENGTH:
        word = next_node.terminal_word
        existing = found.get(word)
        result = WordResult(word.upper(), next_score, path_to_coords(current_path))
        if existing is None or result.dot_score > existing.dot_score:
            found[word] = result

    next_used_mask = used_mask | position_bit
    for next_position in BOARD_NEIGHBORS[position]:
        walk(
            position=next_position,
            node=next_node,
            used_mask=next_used_mask,
            char_count=next_char_count,
            current_score=next_score,
            current_path=current_path,
            tile_texts=tile_texts,
            dot_scores=dot_scores,
            found=found,
        )
    current_path.pop()


def neighbors(row: int, col: int, row_count: int, col_count: int) -> list[tuple[int, int]]:
    result = []
    for row_delta in (-1, 0, 1):
        for col_delta in (-1, 0, 1):
            if row_delta == 0 and col_delta == 0:
                continue
            next_row = row + row_delta
            next_col = col + col_delta
            if 0 <= next_row < row_count and 0 <= next_col < col_count:
                result.append((next_row, next_col))
    return result


def build_board_neighbors() -> tuple[tuple[int, ...], ...]:
    result: list[tuple[int, ...]] = []
    for row in range(4):
        for col in range(4):
            result.append(tuple(next_row * 4 + next_col for next_row, next_col in neighbors(row, col, 4, 4)))
    return tuple(result)


BOARD_NEIGHBORS = build_board_neighbors()


def path_to_coords(path: list[int]) -> list[tuple[int, int]]:
    return [(position // 4, position % 4) for position in path]


@lru_cache(maxsize=1)
def dictionary_trie() -> TrieNode:
    return build_trie(load_dictionary())


def load_dictionary() -> set[str]:
    if not SCOWL_DICTIONARY_PATH.exists():
        raise RuntimeError(f"Missing SCOWL dictionary. Run: python3 scripts/build_scowl_dictionary.py")
    return {
        word
        for word in (line.strip().lower() for line in SCOWL_DICTIONARY_PATH.read_text(encoding="utf-8").splitlines())
        if is_usable_word(word)
    }


def is_usable_word(word: str) -> bool:
    word = word.lower()
    if not (MIN_WORD_LENGTH <= len(word) <= MAX_WORD_LENGTH and word.isalpha()):
        return False
    if word in BLOCKED_WORDS:
        return False

    return True


def consume_tile(node: TrieNode, tile_text: str) -> TrieNode | None:
    if not tile_text:
        return None
    next_node = node
    for letter in tile_text:
        if letter not in next_node:
            return None
        next_node = next_node[letter]
    return next_node


def normalize_tile_text(value: str) -> str:
    text = str(value).strip().replace(" ", "").lower()
    if text in {"q", "qu"}:
        return "qu"
    return text if text.isalpha() else ""


def build_trie(words: set[str]) -> TrieNode:
    root = TrieNode()
    for word in words:
        node = root
        for letter in word:
            node = node.setdefault(letter, TrieNode())
        node.terminal_word = word
    return root


def validate_board(letters: list[list[str]], dots: list[list[int]]) -> None:
    if len(letters) != 4 or any(len(row) != 4 for row in letters):
        raise RuntimeError(f"Expected 4x4 letters, got {letters}")
    if len(dots) != 4 or any(len(row) != 4 for row in dots):
        raise RuntimeError(f"Expected 4x4 dots, got {dots}")
    for row in letters:
        for tile_text in row:
            normalized = normalize_tile_text(tile_text)
            if not (1 <= len(normalized) <= 3 and normalized.isalpha()):
                raise RuntimeError(f"Invalid tile text {tile_text!r} in {letters}")


if __name__ == "__main__":
    raise SystemExit(main())

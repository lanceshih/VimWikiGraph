from pathlib import Path
from typing import Optional

from .parser import (
    Chunk,
    HEADING_RE,
    SEPARATOR_RE,
    _heading_match,
    _tokenize,
    _token_count,
    _split_on_tokens,
)


class MarkdownChunker:
    def __init__(
        self,
        max_tokens: int = 512,
        overlap_tokens: int = 64,
        parent_context_tokens: int = 128,   # heading text injected into child chunks
        min_chunk_tokens: int = 32,          # discard near-empty chunks
    ):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.parent_context_tokens = parent_context_tokens
        self.min_chunk_tokens = min_chunk_tokens

    # ── Public entry point ──────────────────────────────────────────────────

    def chunk_file(self, path: str | Path) -> list[Chunk]:
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        return self.chunk_text(text, source_file=str(path))

    def chunk_text(self, text: str, source_file: str = "") -> list[Chunk]:
        tree = self._parse_tree(text)
        chunks: list[Chunk] = []
        self._walk(tree, heading_path=[], parent_text=None,
                   source_file=source_file, chunks=chunks, counter=[0])
        return chunks

    # ── Tree builder ────────────────────────────────────────────────────────

    def _parse_tree(self, text: str) -> dict:
        """
        Build a recursive tree of sections:
          {
            "heading": str,
            "level": int,
            "body_blocks": [str],   # text/separator blocks before first child
            "children": [node],
          }
        Root node has level=0, heading="".
        """
        # Find all headings with their positions
        heading_matches = list(HEADING_RE.finditer(text))

        root = {"heading": "", "level": 0, "body_blocks": [], "children": []}

        if not heading_matches:
            # No headings at all — treat entire file as one body block
            root["body_blocks"] = self._split_body(text)
            return root

        # Text before the first heading
        preamble = text[: heading_matches[0].start()]
        if preamble.strip():
            root["body_blocks"] = self._split_body(preamble)

        # Stack tracks the open ancestor chain
        stack = [root]

        for i, match in enumerate(heading_matches):
            level, heading = _heading_match(match)

            # Collect body text between this heading and the next
            body_start = match.end()
            body_end = (heading_matches[i + 1].start()
                        if i + 1 < len(heading_matches)
                        else len(text))
            body = text[body_start:body_end]

            node = {
                "heading": heading,
                "level": level,
                "body_blocks": self._split_body(body),
                "children": [],
            }

            # Pop stack until we find a shallower ancestor
            while len(stack) > 1 and stack[-1]["level"] >= level:
                stack.pop()

            stack[-1]["children"].append(node)
            stack.append(node)

        return root

    def _split_body(self, text: str) -> list[str]:
        """Split body text on ---- separators, return non-empty blocks."""
        blocks = SEPARATOR_RE.split(text)
        return [b.strip() for b in blocks if b.strip()]

    # ── Tree walker ─────────────────────────────────────────────────────────

    def _walk(
        self,
        node: dict,
        heading_path: list[str],
        parent_text: Optional[str],
        source_file: str,
        chunks: list[Chunk],
        counter: list[int],         # mutable int via list
    ):
        current_path = heading_path + ([node["heading"]] if node["heading"] else [])
        level = node["level"]

        # Build parent context snippet for child chunks
        own_heading_text = (
            f"{'=' * level} {node['heading']} {'=' * level}\n"
            if node["heading"] else ""
        )

        for block in node["body_blocks"]:
            full_block = own_heading_text + block if own_heading_text else block
            self._emit_block(
                text=full_block,
                heading_path=current_path,
                level=level,
                parent_text=parent_text,
                source_file=source_file,
                chunks=chunks,
                counter=counter,
            )

        # Parent context to pass down: first N tokens of own heading + first body block
        first_body = node["body_blocks"][0] if node["body_blocks"] else ""
        own_context_text = own_heading_text + first_body
        own_context = " ".join(
            _tokenize(own_context_text)[: self.parent_context_tokens]
        ) if own_context_text else parent_text

        for child in node["children"]:
            self._walk(
                child,
                current_path,
                parent_text=own_context,
                source_file=source_file,
                chunks=chunks,
                counter=counter,
            )

    def _emit_block(
        self,
        text: str,
        heading_path: list[str],
        level: int,
        parent_text: Optional[str],
        source_file: str,
        chunks: list[Chunk],
        counter: list[int],
    ):
        if _token_count(text) <= self.max_tokens:
            if _token_count(text) >= self.min_chunk_tokens:
                chunks.append(Chunk(
                    text=text,
                    heading_path=heading_path,
                    heading_level=level,
                    source_file=source_file,
                    chunk_index=counter[0],
                    parent_text=parent_text,
                ))
                counter[0] += 1
        else:
            # Section too large — token-split with overlap
            for sub in _split_on_tokens(text, self.max_tokens, self.overlap_tokens):
                if _token_count(sub) >= self.min_chunk_tokens:
                    chunks.append(Chunk(
                        text=sub,
                        heading_path=heading_path,
                        heading_level=level,
                        source_file=source_file,
                        chunk_index=counter[0],
                        parent_text=parent_text,
                        metadata={"overflow_split": True},
                    ))
                    counter[0] += 1

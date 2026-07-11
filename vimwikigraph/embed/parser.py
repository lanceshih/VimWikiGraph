import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Chunk:
    text: str
    heading_path: list[str]      # e.g. ["Research", "Transformers", "Attention"]
    heading_level: int           # depth of the innermost heading (1–6)
    source_file: str
    chunk_index: int
    parent_text: Optional[str] = None   # first N tokens of parent section
    metadata: dict = field(default_factory=dict)

    @property
    def heading_str(self) -> str:
        return " > ".join(self.heading_path)

    def to_llama_index_document(self):
        """Convert to LlamaIndex Document for direct ingestion."""
        from llama_index.core import Document
        return Document(
            text=self.text,
            metadata={
                "heading_path": self.heading_str,
                "heading_level": self.heading_level,
                "source_file": self.source_file,
                "chunk_index": self.chunk_index,
                **({"parent_text": self.parent_text} if self.parent_text else {}),
                **self.metadata,
            }
        )


# ── Heading regex: vimwiki (= H1 =, == H2 ==) or markdown (# H1, ## H2) ────
HEADING_RE = re.compile(
    r"^(?:(={1,6})\s+(.+?)\s+\1|(#{1,6})\s+(.+?))\s*$", re.MULTILINE
)
SEPARATOR_RE = re.compile(r"^-{4,}\s*$", re.MULTILINE)


def _heading_level(marker: str) -> int:
    return len(marker)


def _heading_match(match: re.Match) -> tuple[int, str]:
    """Return (level, text) from a HEADING_RE match, whichever syntax matched."""
    marker, text = match.group(1), match.group(2)
    if marker is None:
        marker, text = match.group(3), match.group(4)
    return _heading_level(marker), text.strip()


def _tokenize(text: str) -> list[str]:
    """Whitespace tokenizer — swap for a real tokenizer if needed."""
    return text.split()


def _token_count(text: str) -> int:
    return len(_tokenize(text))


def _split_on_tokens(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Fallback splitter for oversized leaf sections."""
    tokens = _tokenize(text)
    chunks, start = [], 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunks.append(" ".join(tokens[start:end]))
        start += max_tokens - overlap_tokens
    return chunks

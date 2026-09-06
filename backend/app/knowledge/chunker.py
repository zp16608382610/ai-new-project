"""Deterministic text normalization and section-aware chunking.

设计原则(Phase 3A):
- 纯标准库、确定性:相同输入 → 完全相同输出(逐字符可复现),便于测试与幂等。
- 优先「章节感知」分段而不是盲目按字符数切分:以 `## 标题` 为段落边界,
  每个 chunk 携带 section 信息。
- 单段内容超过 max_chars 时才按段落边界二次切分,仍是确定性规则。
- 保持简单可替换:后续可平滑替换成其他 chunker,接口不变(见 ingestion)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 章节标题:支持 ## / ### ;# 顶层标题(文档标题)不单独成章
_SECTION_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$")
_DEFAULT_MAX_CHARS = 1200


def normalize_text(raw: str) -> str:
    """行尾统一、去除行尾空白、折叠连续空行——输出稳定可哈希的规范文本。"""
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned: list[str] = []
    prev_blank = False
    for line in lines:
        stripped = line.rstrip()
        if stripped == "":
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        cleaned.append(stripped)
    # 去掉首尾空行
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned)


@dataclass(frozen=True)
class Chunk:
    """One deterministic knowledge chunk (section-aware)."""

    content: str
    section: str | None = None


def _split_long_section(body: str, max_chars: int) -> list[str]:
    """确定性二次切分:优先段落边界,最后按字符硬切。"""
    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            parts.append("\n\n".join(current))
        current = []
        current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if para_len > max_chars:
            flush()
            # 超长单段落按字符切(逐段保证确定性)
            while para:
                parts.append(para[:max_chars])
                para = para[max_chars:]
            continue
        if current and current_len + para_len + 2 > max_chars:
            flush()
        current.append(para)
        current_len += para_len + 2
    flush()
    return parts


def chunk_document(text: str, *, max_chars: int = _DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Section-aware deterministic chunking.

    `## 标题` 开启新 section;`# 文档标题` 与普通段落归入当前 section 的 body。
    连续 index 由调用方在入库时按返回顺序分配。
    """
    normalized = normalize_text(text)
    chunks: list[Chunk] = []
    current_section: str | None = None
    current_body: list[str] = []

    def flush_body() -> None:
        nonlocal current_body
        if current_body:
            body = "\n".join(current_body).strip()
            if body:
                if current_section is not None and len(body) <= max_chars:
                    chunks.append(Chunk(content=body, section=current_section))
                else:
                    for part in _split_long_section(body, max_chars):
                        chunks.append(Chunk(content=part, section=current_section))
        current_body = []

    for line in normalized.split("\n"):
        match = _SECTION_RE.match(line)
        if match:
            flush_body()
            current_section = match.group(1).strip()
            continue
        if line.strip():
            current_body.append(line)
    flush_body()

    if not chunks:
        # 兜底:没有任何章节标题时整体按预算切分
        body = normalized
        for part in _split_long_section(body, max_chars):
            chunks.append(Chunk(content=part))
    return chunks
import re


def tokenize(text: str) -> set[str]:
    """Generate deterministic lexical features for Chinese legal retrieval."""
    normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", text.lower())
    return {
        normalized[i : i + size]
        for size in (2, 3, 4)
        for i in range(len(normalized) - size + 1)
    }

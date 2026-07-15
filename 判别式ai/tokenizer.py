"""最小字符级 tokenizer。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


class CharTokenizer:
    SPECIAL_TOKENS = ("[PAD]", "[UNK]", "[CLS]", "[SEP]")

    def __init__(self, vocab: dict[str, int]):
        self.vocab = vocab

    def __len__(self):
        return len(self.vocab)

    @classmethod
    def build(cls, texts, max_vocab_size: int = 5000):
        counter = Counter(character for text in texts for character in text)
        vocab = {token: index for index, token in enumerate(cls.SPECIAL_TOKENS)}
        for character, _ in counter.most_common(max_vocab_size - len(vocab)):
            if character not in vocab:
                vocab[character] = len(vocab)
        return cls(vocab)

    def encode(self, text: str, max_length: int):
        if max_length < 2:
            raise ValueError("max_length 不能小于 2")
        unk = self.vocab["[UNK]"]
        ids = [self.vocab["[CLS]"]]
        ids.extend(self.vocab.get(character, unk) for character in text[: max_length - 2])
        ids.append(self.vocab["[SEP]"])
        mask = [True] * len(ids)
        padding = max_length - len(ids)
        ids.extend([self.vocab["[PAD]"]] * padding)
        mask.extend([False] * padding)
        return ids, mask

    def save(self, path: Path):
        path.write_text(json.dumps(self.vocab, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path):
        return cls(json.loads(path.read_text(encoding="utf-8")))

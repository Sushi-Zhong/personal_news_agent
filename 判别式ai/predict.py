"""使用训练好的本地 Mini-BERT 输出风险概率和判定。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

from model import MiniBertClassifier
from tokenizer import CharTokenizer
from labels import TAG_TO_NAME


BASE_DIR = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Mini-BERT 本地风险判断")
    parser.add_argument("text", nargs="*", help="待判断文本；省略时进入交互模式")
    parser.add_argument("--artifacts", type=Path, default=BASE_DIR / "artifacts")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        raise ValueError("threshold 必须在 0 到 1 之间")

    config = json.loads((args.artifacts / "config.json").read_text(encoding="utf-8"))
    tokenizer = CharTokenizer.load(args.artifacts / "vocab.json")
    model = MiniBertClassifier(**config)
    model.load_state_dict(torch.load(args.artifacts / "model.pt", map_location="cpu", weights_only=True))
    model.eval()

    def predict(text):
        ids, mask = tokenizer.encode(text, config["max_length"])
        with torch.no_grad():
            violation_logits, tag_logits = model(
                torch.tensor([ids]), torch.tensor([mask], dtype=torch.bool)
            )
            probability = torch.sigmoid(violation_logits).item()
            tag = tag_logits.argmax(dim=1).item() + 1
        label = "不安全" if probability >= args.threshold else "安全"
        print(
            f"判断：{label} | 风险概率：{probability:.2%} "
            f"| tag：{tag}（{TAG_TO_NAME[tag]}）"
        )

    if args.text:
        predict(" ".join(args.text))
        return
    print("输入文本开始判断；直接回车退出。")
    while text := input("> ").strip():
        predict(text)


if __name__ == "__main__":
    main()

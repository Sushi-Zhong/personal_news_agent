"""仅使用本地 test.jsonl 从零训练 Mini-BERT 双任务分类器。"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from model import MiniBertClassifier
from tokenizer import CharTokenizer
from labels import NAME_TO_TAG


BASE_DIR = Path(__file__).resolve().parent


class TextDataset(Dataset):
    def __init__(self, rows, tokenizer: CharTokenizer, max_length: int):
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        text, is_violation, tag = self.rows[index]
        ids, mask = self.tokenizer.encode(text, self.max_length)
        return (
            torch.tensor(ids, dtype=torch.long),
            torch.tensor(mask, dtype=torch.bool),
            torch.tensor(is_violation, dtype=torch.float32),
            torch.tensor(tag - 1, dtype=torch.long),
        )


def parse_args():
    parser = argparse.ArgumentParser(description="从零训练 Mini-BERT 判别模型")
    parser.add_argument("--data", type=Path, default=BASE_DIR / "test.jsonl")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "artifacts")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--max-length", type=int, default=80)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def read_rows(path: Path):
    rows = []
    empty_text_count = 0
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"JSONL 第 {line_number} 行不是有效 JSON") from error
            text = str(row.get("text", "")).strip()
            label = row.get("label")
            subject = row.get("subject")
            if label not in ("违规", "不违规"):
                raise ValueError(f"JSONL 第 {line_number} 行 label 必须是违规或不违规")
            if subject not in NAME_TO_TAG:
                raise ValueError(f"JSONL 第 {line_number} 行 subject 未知: {subject!r}")
            is_violation = int(label == "违规")
            tag = NAME_TO_TAG[subject]
            if is_violation != int(tag != 1):
                raise ValueError(f"JSONL 第 {line_number} 行 label 与 subject 不一致")
            if not text:
                empty_text_count += 1
            rows.append((text, is_violation, tag))
    if len(rows) < 22 or {tag for _, _, tag in rows} != set(range(1, 12)):
        raise ValueError("数据至少需要 22 条，并且必须包含 tag=1 到 tag=11")
    if empty_text_count:
        print(
            f"警告：数据中有 {empty_text_count} 条空文本，"
            "将使用 [CLS][SEP] 作为模型输入"
        )
    return rows


def stratified_split(rows, validation_ratio, seed):
    rng = random.Random(seed)
    groups = {tag: [] for tag in range(1, 12)}
    for row in rows:
        groups[row[2]].append(row)
    train_rows, validation_rows = [], []
    for group in groups.values():
        rng.shuffle(group)
        split_at = max(1, round(len(group) * validation_ratio))
        validation_rows.extend(group[:split_at])
        train_rows.extend(group[split_at:])
    rng.shuffle(train_rows)
    rng.shuffle(validation_rows)
    return train_rows, validation_rows


def choose_device():
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def calculate_metrics(labels, probabilities, threshold=0.5):
    predictions = [int(probability >= threshold) for probability in probabilities]
    tp = sum(p == 1 and y == 1 for p, y in zip(predictions, labels))
    fp = sum(p == 1 and y == 0 for p, y in zip(predictions, labels))
    fn = sum(p == 0 and y == 1 for p, y in zip(predictions, labels))
    correct = sum(p == y for p, y in zip(predictions, labels))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return correct / len(labels), precision, recall, f1


def evaluate(model, loader, violation_loss_fn, tag_loss_fn, device):
    model.eval()
    losses, labels, probabilities, tag_correct, tag_total = [], [], [], 0, 0
    with torch.no_grad():
        for input_ids, attention_mask, batch_labels, batch_tags in loader:
            input_ids = input_ids.to(device)
            attention_mask = attention_mask.to(device)
            batch_labels = batch_labels.to(device)
            batch_tags = batch_tags.to(device)
            violation_logits, tag_logits = model(input_ids, attention_mask)
            loss = violation_loss_fn(violation_logits, batch_labels) + tag_loss_fn(tag_logits, batch_tags)
            losses.append(loss.item())
            labels.extend(batch_labels.cpu().int().tolist())
            probabilities.extend(torch.sigmoid(violation_logits).cpu().tolist())
            tag_correct += (tag_logits.argmax(dim=1) == batch_tags).sum().item()
            tag_total += len(batch_tags)
    metrics = calculate_metrics(labels, probabilities)
    return sum(losses) / len(losses), metrics, tag_correct / tag_total


def main():
    args = parse_args()
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    rows = read_rows(args.data)
    train_rows, validation_rows = stratified_split(rows, 0.2, args.seed)

    # 词表只能用训练集构建，避免验证集信息泄漏。
    tokenizer = CharTokenizer.build([text for text, _, _ in train_rows])
    train_loader = DataLoader(
        TextDataset(train_rows, tokenizer, args.max_length),
        batch_size=args.batch_size,
        shuffle=True,
    )
    validation_loader = DataLoader(
        TextDataset(validation_rows, tokenizer, args.max_length),
        batch_size=args.batch_size,
    )

    config = {
        "vocab_size": len(tokenizer),
        "max_length": args.max_length,
        "hidden_size": 128,
        "num_heads": 4,
        "num_layers": 2,
        "feedforward_size": 256,
        "dropout": 0.1,
        "num_tags": 11,
    }
    device = choose_device()
    model = MiniBertClassifier(**config).to(device)
    violation_loss_fn = nn.BCEWithLogitsLoss()
    tag_loss_fn = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)

    args.output.mkdir(parents=True, exist_ok=True)
    tokenizer.save(args.output / "vocab.json")
    (args.output / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    best_score = -1.0
    best_f1 = 0.0
    best_tag_accuracy = 0.0
    print(f"设备：{device} | 训练：{len(train_rows)} | 验证：{len(validation_rows)}")
    print(f"词表：{len(tokenizer)} | 参数量：{sum(p.numel() for p in model.parameters()):,}")

    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        for input_ids, attention_mask, labels, tags in train_loader:
            input_ids = input_ids.to(device)
            attention_mask = attention_mask.to(device)
            labels = labels.to(device)
            tags = tags.to(device)
            optimizer.zero_grad()
            violation_logits, tag_logits = model(input_ids, attention_mask)
            loss = violation_loss_fn(violation_logits, labels) + tag_loss_fn(tag_logits, tags)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += loss.item()

        validation_loss, (accuracy, precision, recall, f1), tag_accuracy = evaluate(
            model, validation_loader, violation_loss_fn, tag_loss_fn, device
        )
        print(
            f"Epoch {epoch:02d} | train_loss={total_loss / len(train_loader):.4f} "
            f"| val_loss={validation_loss:.4f} | acc={accuracy:.3f} "
            f"| precision={precision:.3f} | recall={recall:.3f} | f1={f1:.3f} "
            f"| tag_acc={tag_accuracy:.3f}"
        )
        # 同时考虑两个任务，避免只保存二分类表现较好的模型。
        score = (f1 + tag_accuracy) / 2
        if score > best_score:
            best_score = score
            best_f1 = f1
            best_tag_accuracy = tag_accuracy
            torch.save(model.state_dict(), args.output / "model.pt")

    print(f"最佳验证 F1：{best_f1:.3f} | 最佳模型 tag_acc：{best_tag_accuracy:.3f}")
    print(f"模型、配置和词表已保存到：{args.output}")


if __name__ == "__main__":
    main()

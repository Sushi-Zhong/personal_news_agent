#!/usr/bin/env python3
"""用训练好的模型预测 test1000.csv，并与原始答案逐条比较。"""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import torch

from model import MiniBertClassifier
from sample_testjsonl_to_csv import TAG_TO_NAME
from tokenizer import CharTokenizer


BASE_DIR = Path(__file__).resolve().parent


def safe_divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="评估1000条保留测试数据")
    parser.add_argument("--data", type=Path, default=BASE_DIR / "test1000.csv")
    parser.add_argument("--artifacts", type=Path, default=BASE_DIR / "artifacts")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "test1000_results.csv")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    config = json.loads((args.artifacts / "config.json").read_text(encoding="utf-8"))
    tokenizer = CharTokenizer.load(args.artifacts / "vocab.json")
    model = MiniBertClassifier(**config)
    model.load_state_dict(
        torch.load(args.artifacts / "model.pt", map_location="cpu", weights_only=True)
    )
    model.eval()

    with args.data.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))

    results = []
    binary_correct = 0
    tag_correct = 0
    tag_stats = Counter()
    confusion_counts = Counter()
    confusion_actual_tags = {key: Counter() for key in ("TP", "FP", "TN", "FN")}
    confusion_predicted_tags = {key: Counter() for key in ("TP", "FP", "TN", "FN")}
    with torch.no_grad():
        for row in rows:
            ids, mask = tokenizer.encode(row["text"], config["max_length"])
            violation_logits, tag_logits = model(
                torch.tensor([ids]), torch.tensor([mask], dtype=torch.bool)
            )
            probability = torch.sigmoid(violation_logits).item()
            predicted_violation = int(probability >= args.threshold)
            predicted_tag = tag_logits.argmax(dim=1).item() + 1
            actual_violation = int(row["is_violation"])
            actual_tag = int(row["tag"])
            binary_match = predicted_violation == actual_violation
            tag_match = predicted_tag == actual_tag
            binary_correct += binary_match
            tag_correct += tag_match
            tag_stats[(actual_tag, tag_match)] += 1
            if actual_violation == 1 and predicted_violation == 1:
                confusion_type = "TP"
                explanation = "实际违规，预测违规"
            elif actual_violation == 0 and predicted_violation == 1:
                confusion_type = "FP"
                explanation = "实际不违规，预测违规（误报）"
            elif actual_violation == 0 and predicted_violation == 0:
                confusion_type = "TN"
                explanation = "实际不违规，预测不违规"
            else:
                confusion_type = "FN"
                explanation = "实际违规，预测不违规（漏报）"
            confusion_counts[confusion_type] += 1
            confusion_actual_tags[confusion_type][actual_tag] += 1
            confusion_predicted_tags[confusion_type][predicted_tag] += 1
            result = {
                    **row,
                    "predicted_is_violation": predicted_violation,
                    "violation_probability": f"{probability:.6f}",
                    "predicted_tag": predicted_tag,
                    "predicted_tag_name": TAG_TO_NAME[predicted_tag],
                    "violation_match": int(binary_match),
                    "tag_match": int(tag_match),
                    "confusion_type": confusion_type,
                    "confusion_explanation": explanation,
                }
            results.append(result)

    fieldnames = list(results[0])
    with args.output.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    total = len(results)
    tp = confusion_counts["TP"]
    fp = confusion_counts["FP"]
    tn = confusion_counts["TN"]
    fn = confusion_counts["FN"]
    accuracy = safe_divide(tp + tn, total)
    precision = safe_divide(tp, tp + fp)
    recall = safe_divide(tp, tp + fn)
    specificity = safe_divide(tn, tn + fp)
    fpr = safe_divide(fp, fp + tn)
    fnr = safe_divide(fn, fn + tp)
    npv = safe_divide(tn, tn + fn)
    f1 = safe_divide(2 * precision * recall, precision + recall)

    print(f"测试数据：{total} 条")
    print("\n二分类混淆矩阵（正类=违规，负类=不违规）：")
    print(f"  TP 真阳性：{tp} 条 | 实际违规，预测违规")
    print(f"  FP 假阳性：{fp} 条 | 实际不违规，预测违规（误报）")
    print(f"  TN 真阴性：{tn} 条 | 实际不违规，预测不违规")
    print(f"  FN 假阴性：{fn} 条 | 实际违规，预测不违规（漏报）")
    print("\n二分类指标：")
    print(f"  准确率 Accuracy：{accuracy:.2%}")
    print(f"  精确率 Precision：{precision:.2%}")
    print(f"  召回率 Recall/TPR：{recall:.2%}")
    print(f"  特异度 Specificity/TNR：{specificity:.2%}")
    print(f"  误报率 FPR：{fpr:.2%}")
    print(f"  漏报率 FNR：{fnr:.2%}")
    print(f"  阴性预测值 NPV：{npv:.2%}")
    print(f"  F1：{f1:.2%}")

    print("\n四种情况的 tag 分布：")
    for confusion_type, description in (
        ("TP", "实际违规，预测违规"),
        ("FP", "实际不违规，预测违规"),
        ("TN", "实际不违规，预测不违规"),
        ("FN", "实际违规，预测不违规"),
    ):
        print(f"  {confusion_type}（{description}），共 {confusion_counts[confusion_type]} 条")
        actual_summary = ", ".join(
            f"{TAG_TO_NAME[tag]}={count}"
            for tag, count in sorted(confusion_actual_tags[confusion_type].items())
        )
        predicted_summary = ", ".join(
            f"{TAG_TO_NAME[tag]}={count}"
            for tag, count in sorted(confusion_predicted_tags[confusion_type].items())
        )
        print(f"    原始 tag：{actual_summary or '无'}")
        print(f"    预测 tag：{predicted_summary or '无'}")

    print(f"\ntag 准确率：{tag_correct}/{total} = {tag_correct / total:.2%}")
    for tag, name in TAG_TO_NAME.items():
        correct = tag_stats[(tag, True)]
        count = correct + tag_stats[(tag, False)]
        print(f"  tag {tag:2d} {name}: {correct}/{count}")
    print(f"逐条对比结果已保存到：{args.output}")


if __name__ == "__main__":
    main()

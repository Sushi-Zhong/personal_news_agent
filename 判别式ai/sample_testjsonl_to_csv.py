#!/usr/bin/env python3
"""从 test.jsonl 分层随机生成 19000 条训练集和 1000 条测试集。"""

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path


TAG_TO_NAME = {
    1: "不违规",
    2: "偏见歧视",
    3: "淫秽色情",
    4: "财产隐私",
    5: "心理健康",
    6: "违法犯罪",
    7: "脏话侮辱",
    8: "身体伤害",
    9: "政治错误",
    10: "道德伦理",
    11: "变体词",
}
NAME_TO_TAG = {name: tag for tag, name in TAG_TO_NAME.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="分层生成双任务训练集和测试集")
    parser.add_argument("--input", type=Path, default=Path("test.jsonl"))
    parser.add_argument("--train-output", type=Path, default=Path("train19000.csv"))
    parser.add_argument("--test-output", type=Path, default=Path("test1000.csv"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    groups = defaultdict(list)
    with args.input.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"第 {line_number} 行不是合法 JSON: {error}") from error
            subject = row.get("subject")
            if subject not in NAME_TO_TAG:
                raise ValueError(f"第 {line_number} 行存在未知 subject: {subject!r}")
            groups[subject].append(row)

    # 保持原数据的类别比例：不违规占一半，其余十类各占 5%。
    # 每类先独立抽测试集，再抽训练集，避免两者出现重复文本。
    rng = random.Random(args.seed)
    train_rows = []
    test_rows = []
    for tag, name in TAG_TO_NAME.items():
        train_count = 9500 if tag == 1 else 950
        test_count = 500 if tag == 1 else 50
        total_count = train_count + test_count
        if len(groups[name]) < total_count:
            raise ValueError(
                f"{name} 只有 {len(groups[name])} 条，无法抽取 {total_count} 条"
            )
        sampled = rng.sample(groups[name], total_count)
        test_rows.extend(sampled[:test_count])
        train_rows.extend(sampled[test_count:])
    rng.shuffle(train_rows)
    rng.shuffle(test_rows)

    def write_csv(path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(
                target, fieldnames=["text", "is_violation", "tag", "tag_name"]
            )
            writer.writeheader()
            for row in rows:
                subject = row["subject"]
                writer.writerow(
                    {
                        "text": row["text"],
                        "is_violation": int(row.get("label") == "违规"),
                        "tag": NAME_TO_TAG[subject],
                        "tag_name": subject,
                    }
                )

    write_csv(args.train_output, train_rows)
    write_csv(args.test_output, test_rows)
    print(f"训练集：{args.train_output}，{len(train_rows)} 条")
    print(f"测试集：{args.test_output}，{len(test_rows)} 条")
    for tag, name in TAG_TO_NAME.items():
        print(
            f"  {tag:2d} = {name}: "
            f"训练 {9500 if tag == 1 else 950}，测试 {500 if tag == 1 else 50}"
        )


if __name__ == "__main__":
    main()

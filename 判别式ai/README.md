# Mini-BERT 判别模型

这是一个从零训练的小型 PyTorch 文本安全分类器，不使用 MacBERT、Hugging Face 或任何预训练权重。

模型包含字符级 tokenizer、两层 Transformer Encoder，以及“是否违规”和“11 类 subject”两个输出头。

## 数据

项目只使用同目录的 `test.jsonl`，每行格式为：

```json
{"text":"待判断文本","label":"违规","subject":"脏话侮辱"}
```

训练时会按 11 个 subject 分层，随机分出 20% 作为验证集。

## 训练

```bash
source .venv/bin/activate
python train.py
```

可选参数：

```bash
python train.py --epochs 20 --batch-size 8 --learning-rate 0.0003
```

训练产物保存在 `artifacts/`。

## 预测

```bash
python predict.py "请判断这段文本"
python predict.py --threshold 0.4 "待判断文本"
```

该模型仅用于演示小型判别式模型的完整训练流程，不应直接用于生产内容审核。

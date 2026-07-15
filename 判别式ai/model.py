"""从零构建的 Mini-BERT 风格判别式模型，不加载预训练权重。"""

from __future__ import annotations

import torch
from torch import nn


class MiniBertClassifier(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        max_length: int = 80,
        hidden_size: int = 128,
        num_heads: int = 4,
        num_layers: int = 2,
        feedforward_size: int = 256,
        dropout: float = 0.1,
        num_tags: int = 11,
    ):
        super().__init__()
        if hidden_size % num_heads != 0:
            raise ValueError("hidden_size 必须能被 num_heads 整除")

        # BERT 的输入表示：Token Embedding + Position Embedding。
        self.token_embedding = nn.Embedding(vocab_size, hidden_size, padding_idx=0)
        self.position_embedding = nn.Embedding(max_length, hidden_size)
        self.embedding_norm = nn.LayerNorm(hidden_size)
        self.embedding_dropout = nn.Dropout(dropout)

        # 多层双向 Transformer Encoder：自注意力、前馈网络、残差和归一化。
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=hidden_size,
            nhead=num_heads,
            dim_feedforward=feedforward_size,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)

        # 两个任务共享编码器：一个判断是否违规，一个预测 11 类 tag。
        self.violation_classifier = nn.Linear(hidden_size, 1)
        self.tag_classifier = nn.Linear(hidden_size, num_tags)
        self.output_dropout = nn.Dropout(dropout)
        self.max_length = max_length

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        batch_size, sequence_length = input_ids.shape
        positions = torch.arange(sequence_length, device=input_ids.device)
        positions = positions.unsqueeze(0).expand(batch_size, -1)

        hidden = self.token_embedding(input_ids) + self.position_embedding(positions)
        hidden = self.embedding_dropout(self.embedding_norm(hidden))
        hidden = self.encoder(hidden, src_key_padding_mask=~attention_mask.bool())
        cls_vector = hidden[:, 0, :]
        cls_vector = self.output_dropout(cls_vector)
        violation_logits = self.violation_classifier(cls_vector).squeeze(-1)
        tag_logits = self.tag_classifier(cls_vector)
        return violation_logits, tag_logits

    def predict_probability(
        self, input_ids: torch.Tensor, attention_mask: torch.Tensor
    ) -> torch.Tensor:
        """Sigmoid 是输出限定层，把任意 logit 限制为 0~1 风险概率。"""
        violation_logits, _ = self(input_ids, attention_mask)
        return torch.sigmoid(violation_logits)

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


LLM_QUERY_MODERATION_SERVICE = "llm_query_moderation"


class ContentModerationError(RuntimeError):
    # 内容安全检测本身不可用时抛出，例如缺少密钥、缺少 SDK 或阿里云返回结构异常。
    pass


@dataclass(frozen=True)
class ContentModerationResult:
    # 统一返回给业务层的检测结果，业务层只需要优先看 allowed。
    allowed: bool
    code: int | str | None
    message: str
    risk_level: str | None
    label: str | None
    description: str | None
    request_id: str | None
    raw: dict[str, Any]


class TextModerationPlusService:
    """阿里云内容安全 TextModerationPlus 的轻量封装。"""

    def __init__(
        self,
        access_key_id: str | None = None,
        access_key_secret: str | None = None,
        endpoint: str | None = None,
        query_service: str = LLM_QUERY_MODERATION_SERVICE,
    ):
        # 优先使用显式传参；没有传参时，读取本地环境变量，兼容项目里已有的阿里云密钥命名。
        self.access_key_id = (
            access_key_id
            or os.getenv("ALIYUN_ACCESS_KEY_ID")
            or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
            or os.getenv("AccessKeyID")
        )
        self.access_key_secret = (
            access_key_secret
            or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
            or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
            or os.getenv("AccessKeySecret")
        )
        # TextModerationPlus 调试成功时用的是 green-cip.cn-shanghai.aliyuncs.com，可通过环境变量覆盖。
        self.endpoint = endpoint or os.getenv("ALIYUN_CONTENT_MODERATION_ENDPOINT", "green-cip.cn-shanghai.aliyuncs.com")
        # 用户输入和模型输出分别使用不同审核服务类型，必要时可以用环境变量分别覆盖。
        self.query_service = os.getenv("ALIYUN_CONTENT_MODERATION_QUERY_SERVICE", query_service)

    @property
    def configured(self) -> bool:
        return bool(self.access_key_id and self.access_key_secret)

    def check_query_text(self, text: str) -> ContentModerationResult:
        # 用户发给大模型之前的输入检测，Service=llm_query_moderation。
        return self._check_text_with_service(text, self.query_service)

    def _check_text_with_service(self, text: str, service: str) -> ContentModerationResult:
        if not self.configured:
            raise ContentModerationError("Aliyun content moderation access key is not configured")
        if not text.strip():
            # 空输入没有必要调用远端 API，直接视为通过。
            return ContentModerationResult(
                allowed=True,
                code=200,
                message="empty input",
                risk_level="none",
                label="nonLabel",
                description="empty input skipped",
                request_id=None,
                raw={},
            )

        payload = self._call_text_moderation_plus(text, service)
        return self._parse_result(payload)

    def _parse_result(self, payload: dict[str, Any]) -> ContentModerationResult:
        data = payload.get("Data") or {}
        results = data.get("Result") or []
        first = results[0] if results and isinstance(results[0], dict) else {}
        code = payload.get("Code")
        risk_level = data.get("RiskLevel")
        label = first.get("Label")
        description = first.get("Description")
        # 当前按控制台验证过的安全返回判断：无风险且无标签才允许继续进入聊天流程。
        allowed = code == 200 and risk_level == "none" and label == "nonLabel"
        return ContentModerationResult(
            allowed=allowed,
            code=code,
            message=str(payload.get("Message") or ""),
            risk_level=risk_level,
            label=label,
            description=description,
            request_id=payload.get("RequestId"),
            raw=payload,
        )

    def _call_text_moderation_plus(self, text: str, service: str) -> dict[str, Any]:
        try:
            # 放在函数内部导入，避免未安装内容安全 SDK 时影响整个应用启动。
            from alibabacloud_tea_openapi import models as openapi_models
            from alibabacloud_green20220302.client import Client as GreenClient
            from alibabacloud_green20220302 import models as green_models
        except ImportError as exc:
            raise ContentModerationError(
                "Aliyun TextModerationPlus requires alibabacloud_green20220302 and alibabacloud_tea_openapi"
            ) from exc

        config = openapi_models.Config(
            access_key_id=self.access_key_id,
            access_key_secret=self.access_key_secret,
            endpoint=self.endpoint,
        )
        client = GreenClient(config)
        request = green_models.TextModerationPlusRequest(
            service=service,
            # 阿里云接口要求 ServiceParameters 是 JSON 字符串，不是 Python dict。
            service_parameters=json.dumps({"content": text}, ensure_ascii=False),
        )
        response = client.text_moderation_plus(request)
        body = getattr(response, "body", None)
        if body is None:
            raise ContentModerationError("Aliyun TextModerationPlus response body is empty")
        if hasattr(body, "to_map"):
            return body.to_map()
        if isinstance(body, dict):
            return body
        raise ContentModerationError(f"Unsupported Aliyun response body type: {type(body).__name__}")
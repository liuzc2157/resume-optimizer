"""配额口径与参数校验的回归测试（纯逻辑，不依赖数据库）。

覆盖两个线上缺陷：
1. `/match?use_rag=true` 与 `/rag/retrieve` 都会调用 LLM，却不受每日配额约束——
   免费档可以无限消耗真实 token 成本；
2. `intensity` 是裸字符串，传 `ENHANCE` / `foo` 不报错，静默退化成默认提示词，
   同时还在缓存键里占了一个独立分区。
"""

import pytest
from pydantic import ValidationError

from app.rate_limit import QUOTA_ENDPOINTS
from app.schemas import OptimizeRequest


class TestQuotaEndpoints:
    """配额只覆盖「真的会调 LLM」的接口。"""

    @pytest.mark.parametrize(
        "endpoint",
        ["/optimize", "/chat", "/compare", "/match", "/rag/retrieve"],
    )
    def test_llm_endpoints_consume_quota(self, endpoint):
        assert endpoint in QUOTA_ENDPOINTS, f"{endpoint} 会调 LLM，必须计入配额"

    def test_keyword_only_match_is_free(self):
        """纯关键词匹配是零成本计算，单独记账、不占配额。"""
        assert "/match/keyword" not in QUOTA_ENDPOINTS


class TestIntensityValidation:
    """改写强度必须是三档之一，非法值直接 422，而不是静默退化。"""

    @pytest.mark.parametrize("value", ["light", "enhance", "rewrite"])
    def test_accepts_valid_values(self, value):
        req = OptimizeRequest(resume_text="简历", intensity=value)
        assert req.intensity == value

    def test_defaults_to_enhance(self):
        assert OptimizeRequest(resume_text="简历").intensity == "enhance"

    @pytest.mark.parametrize("value", ["ENHANCE", "foo", "", "  ", "enhance "])
    def test_rejects_invalid_values(self, value):
        with pytest.raises(ValidationError):
            OptimizeRequest(resume_text="简历", intensity=value)

    def test_rejects_null(self):
        """非 Optional：显式传 null 也应该被拒，避免悄悄用默认值。"""
        with pytest.raises(ValidationError):
            OptimizeRequest(resume_text="简历", intensity=None)

"""LLM 客户端封装（可插拔：任意 OpenAI 兼容接口 + 内置免费平台预设）。

所有供应商都走 OpenAI 兼容协议，因此统一复用 openai SDK，
只需配置 base_url / model / api_key 三件套即可切换。

免费优先的内置预设（2026-09 核实）
--------------------------------
    glm          智谱 glm-4-flash           —— 官方承诺永久免费，首选
    modelscope   魔搭 Tencent-Hunyuan/Hy3   —— 每日约 2000 次免费调用
    siliconflow  硅基流动 Qwen/Qwen3-8B     —— 免费档（需实名，有 RPM 限制）
    deepseek     DeepSeek deepseek-chat     —— 新用户赠送额度
    novita       Novita tencent/hy3         —— 上线期曾 $0/M，需确认现价
    openrouter   OpenRouter tencent/hy3     —— hy3 免费期已于 2026-07-21 结束

也可以完全自定义：设置 LLM_PROVIDER=custom 并给出
LLM_BASE_URL / LLM_MODEL / LLM_API_KEY 即可接入任何 OpenAI 兼容服务
（vLLM、Ollama、LocalAI、公司内网网关等）。

环境变量优先级：显式 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY > 预设默认值。
"""

import os
from typing import List, Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 离线/本地验证开关：设置 MOCK_LLM=1 时返回固定文本，无需真实 API Key。
# 用于本地起服务、跑缓存命中与智能匹配的全链路验证。
MOCK_LLM: bool = os.getenv("MOCK_LLM", "").strip().lower() in ("1", "true", "yes", "on")


def _mock_text(system: str, user: str) -> str:
    """生成一个可用于缓存/匹配验证的固定文本（含可解析的 SCORE）。"""
    return (
        "SCORE: 88\n\n"
        "[模拟分析报告] 该简历与目标岗位在 Python / MySQL / 后端开发经验上匹配度较高；"
        "建议在项目经历中补充量化指标（如 QPS、数据规模），并突出容器化与微服务实践。\n\n"
        "[模拟改写建议] 一、将「熟悉 FastAPI」改写为「独立基于 FastAPI 设计日均百万级请求的高并发 API 网关」；"
        "二、为 MySQL 优化经历补充「慢查询从 2s 降至 50ms」等量化结果。"
    )


def _mock_message(messages: List[dict]):
    """返回一个模仿 OpenAI 响应的对象，供 call_llm_messages 离线使用。"""
    import types

    # 取最后一条 user 消息，让 mock 内容与输入相关（便于观察多轮是否串味）
    last_user = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            last_user = str(m.get("content", ""))
            break
    msg = types.SimpleNamespace(content=_mock_text("", last_user), tool_calls=None)
    choice = types.SimpleNamespace(message=msg)
    return types.SimpleNamespace(choices=[choice])


# 各供应商的默认接入参数（均兼容 OpenAI 协议）
# 默认模型优先选「免费档」，避免一上手就产生费用。
PROVIDER_DEFAULTS: dict = {
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-4-flash",  # 智谱官方永久免费；如需更强能力可改 glm-4-plus 等付费模型
        "api_key_env": "GLM_API_KEY",
        "note": "智谱 AI：注册并实名后领取免费额度，glm-4-flash 永久免费",
    },
    "modelscope": {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "model": "Tencent-Hunyuan/Hy3",  # 也可换 Qwen/Qwen3.5-35B-A3B 等魔搭托管模型
        "api_key_env": "MODELSCOPE_API_KEY",
        "note": "魔搭社区：一个 Key 兼容 OpenAI 协议，每日约 2000 次免费调用",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        # 免费档（需实名，有 RPM 限制）：Qwen3-8B / GLM-Z1-9B-0414 / GLM-4-9B-0414
        "model": "Qwen/Qwen3-8B",
        "api_key_env": "SILICONFLOW_API_KEY",
        "note": "硅基流动：部分小模型免费，注册送额度；免费档已收缩，以官网定价为准",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "note": "DeepSeek：新用户赠送体验额度",
    },
    "novita": {
        "base_url": "https://api.novita.ai/openai",
        "model": "tencent/hy3",
        "api_key_env": "NOVITA_API_KEY",
        "note": "Novita AI：Hy3 上线期曾为 $0/M tokens，使用前请确认当前价格",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "tencent/hy3",
        "api_key_env": "OPENROUTER_API_KEY",
        "note": "OpenRouter：注意 tencent/hy3 的免费期已于 2026-07-21 结束",
    },
}

_FREE_HINT = (
    "免费 Key 获取：\n"
    "  1) 智谱 glm-4-flash（永久免费，推荐）：open.bigmodel.cn 注册实名 → API 密钥管理\n"
    "  2) 魔搭 ModelScope（每日约 2000 次免费）：modelscope.cn 账号 → API Token\n"
    "  3) 硅基流动 SiliconFlow（部分小模型免费，需实名）：cloud.siliconflow.cn\n"
    "拿到 Key 后设置 LLM_PROVIDER 与对应 *_API_KEY，或直接用 MOCK_LLM=1 离线跑通流程。"
)

_llm_provider: str = os.getenv("LLM_PROVIDER", "glm").strip().lower()

if _llm_provider in PROVIDER_DEFAULTS:
    _defaults = PROVIDER_DEFAULTS[_llm_provider]
else:
    # 未知供应商 -> 视为自定义 OpenAI 兼容服务，必须显式给出 base_url
    _defaults = {
        "base_url": os.getenv("LLM_BASE_URL", ""),
        "model": os.getenv("LLM_MODEL", ""),
        "api_key_env": "LLM_API_KEY",
        "note": "自定义 OpenAI 兼容服务",
    }

LLM_BASE_URL: str = os.getenv("LLM_BASE_URL") or _defaults["base_url"]
LLM_MODEL: str = os.getenv("LLM_MODEL") or _defaults["model"]

if not LLM_BASE_URL:
    raise RuntimeError(
        f"LLM_PROVIDER={_llm_provider!r} 未知且未设置 LLM_BASE_URL。"
        f"可选预设：{', '.join(PROVIDER_DEFAULTS)}，或用 custom + LLM_BASE_URL 自定义。\n"
        + _FREE_HINT
    )

# 模块级单例客户端：OpenAI SDK 自带连接池，进程内复用即可
_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    """获取（懒初始化的）OpenAI 兼容客户端，指向当前供应商。"""
    global _client
    if _client is None:
        # 优先 LLM_API_KEY，其次按供应商读取专属变量（GLM_API_KEY / DEEPSEEK_API_KEY）
        api_key = os.getenv("LLM_API_KEY") or os.getenv(_defaults["api_key_env"])
        if not api_key:
            raise RuntimeError(
                f"缺少 API Key：请设置 LLM_API_KEY 或 {_defaults['api_key_env']}"
                f"（当前 LLM_PROVIDER={_llm_provider}，详见 .env.example）。\n" + _FREE_HINT
            )
        _client = OpenAI(api_key=api_key, base_url=LLM_BASE_URL)
    return _client


def call_llm(system: str, user: str, temperature: float = 0.3) -> str:
    """调用当前供应商的对话接口，返回模型输出的文本。

    Args:
        system: system prompt，约定模型角色与输出格式。
        user: user 消息，携带本次任务的具体输入。
        temperature: 采样温度，简历改写场景偏确定性，默认 0.3。

    Returns:
        模型返回的第一条回复文本。

    Raises:
        RuntimeError: API Key 未配置。
        openai.OpenAIError: API 调用失败（网络/鉴权/限流等）。
    """
    if MOCK_LLM:
        return _mock_text(system, user)
    client = get_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    content = response.choices[0].message.content
    return content.strip() if content else ""


def call_llm_messages(
    messages: List[dict], tools: Optional[List[dict]] = None, temperature: float = 0.3
):
    """带工具定义的对话调用，返回原始响应消息（可能包含 tool_calls）。

    供对话型 Agent 的 agent_node 使用：调用方需要检查返回对象的
    tool_calls 属性来决定下一步路由，因此这里不做文本剥离。
    """
    if MOCK_LLM:
        # 与真实路径保持一致的返回形状：返回 message（含 .content / .tool_calls）。
        # 注：此处曾误用未定义的 system/user 变量、且返回了 response 而非 message，
        #     导致离线模式下 /chat 直接抛 NameError / AttributeError。
        return _mock_message(messages).choices[0].message
    client = get_client()
    kwargs: dict = dict(model=LLM_MODEL, messages=messages, temperature=temperature)
    if tools:
        kwargs["tools"] = tools
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message


def call_llm_with_history(messages: List[dict], temperature: float = 0.3) -> str:
    """支持完整消息历史的调用方式，供未来多轮对话/人审节点扩展使用。"""
    if MOCK_LLM:
        return _mock_text(str(messages), "")
    client = get_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=temperature,
    )
    content = response.choices[0].message.content
    return content.strip() if content else ""

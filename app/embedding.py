"""文本向量化（供语义缓存与语义匹配使用）。

设计要点
--------
1. **跨进程稳定**：向量必须可持久化到 MySQL 并在重启后与新请求比较，
   因此哈希函数用 md5（稳定），**不能用 Python 内置 hash()**（受
   PYTHONHASHSEED 随机化影响，重启后向量失效）。
2. **零依赖优先**：默认走本地「分词 + 特征哈希 + 次线性 TF + L2 归一化」，
   不下载任何模型即可工作，适合离线与内网。
3. **可插拔**：如配置了兼容 OpenAI 的 embedding 接口（EMBEDDING_BASE_URL /
   EMBEDDING_API_KEY / EMBEDDING_MODEL），自动切换为接口向量，语义更准。

环境变量
--------
    EMBEDDING_PROVIDER   local(默认) | api
    EMBEDDING_DIM        本地向量维度，默认 256
    EMBEDDING_BASE_URL   兼容 OpenAI 的 base_url（api 模式）
    EMBEDDING_API_KEY    api 模式的密钥
    EMBEDDING_MODEL      api 模式的模型名
"""

import hashlib
import logging
import math
import re
import unicodedata
from typing import List, Sequence

logger = logging.getLogger(__name__)

# 常用 embedding 服务预设（均兼容 OpenAI /embeddings 协议）
# 免费档以官网实时定价为准；硅基流动的 BAAI/bge-m3 长期列在免费向量模型中。
EMBEDDING_PRESETS: dict = {
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "BAAI/bge-m3",
        "key_env": "SILICONFLOW_API_KEY",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "embedding-3",
        "key_env": "GLM_API_KEY",
    },
    "modelscope": {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "model": "BAAI/bge-m3",
        "key_env": "MODELSCOPE_API_KEY",
    },
}

# 归一化时保留中日韩文字、字母数字，其余视为分隔符
_KEEP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff]+")


def normalize(text: str) -> str:
    """缓存键/向量前的文本归一化：全角转半角、小写、压缩空白与标点噪音。

    目的：让「多了几个空格」「大小写不同」「标点差异」这类无语义差别的改动
    仍能命中同一份缓存（行业实践里称为 query normalization）。
    """
    if not text:
        return ""
    # 全角 -> 半角
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    # 非字母数字/中日韩字符统一视为空格
    text = _KEEP_RE.sub(" ", text)
    # 压缩空白
    return " ".join(text.split())


def tokenize(text: str) -> List[str]:
    """中文优先用 jieba 分词；不可用时退化为字符 bigram（对中文仍有效）。"""
    text = normalize(text)
    if not text:
        return []
    try:  # pragma: no cover - 依赖可选
        import jieba  # type: ignore

        return [t for t in jieba.cut(text) if t.strip()]
    except Exception:  # noqa: BLE001
        # 退化：CJK 字符 bigram + 连续英文数字串
        toks: List[str] = []
        buf = ""
        for ch in text:
            if "\u4e00" <= ch <= "\u9fff":
                if buf:
                    toks.append(buf)
                    buf = ""
                toks.append(ch)
            elif ch.isalnum():
                buf += ch
            else:
                if buf:
                    toks.append(buf)
                    buf = ""
        if buf:
            toks.append(buf)
        # 补 bigram，让"后端开发"与"后端 开发"能对齐
        cjk = [t for t in toks if len(t) == 1 and "\u4e00" <= t <= "\u9fff"]
        if len(cjk) >= 2:
            toks += [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
        return toks


class LocalEmbedder:
    """本地哈希向量：分词 -> 特征哈希 -> 次线性 TF -> L2 归一化。

    对「近似重复文本」（改了几个字、调了语序）相似度很高，
    对语义不同但用词相近的文本相似度中等——因此调用方必须配合
    **高阈值 + 同分区检索** 使用，避免误命中。
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in tokenize(text):
            # md5 保证跨进程/跨重启稳定
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        # 次线性 TF，抑制长文本里高频词的支配
        vec = [(1.0 + math.log(v)) if v > 0 else 0.0 for v in vec]
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class APIEmbedder:
    """兼容 OpenAI 的 /embeddings 接口向量（语义更准，需联网与密钥）。"""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        from openai import OpenAI  # 延迟导入，本地模式无需安装

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    def embed(self, text: str) -> List[float]:
        resp = self._client.embeddings.create(model=self._model, input=text[:8000])
        return list(resp.data[0].embedding)


_embedder = None


def get_embedder():
    """按配置返回 embedder 单例。"""
    global _embedder
    if _embedder is not None:
        return _embedder
    import os

    provider = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()
    base_url = api_key = model = ""
    if provider == "api":
        # 完全自定义：显式给出三件套
        base_url = os.getenv("EMBEDDING_BASE_URL", "")
        api_key = os.getenv("EMBEDDING_API_KEY", "")
        model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    elif provider in EMBEDDING_PRESETS:
        # 内置预设（siliconflow / glm / modelscope），显式 env 可覆盖
        preset = EMBEDDING_PRESETS[provider]
        base_url = os.getenv("EMBEDDING_BASE_URL") or preset["base_url"]
        api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv(preset["key_env"], "")
        model = os.getenv("EMBEDDING_MODEL") or preset["model"]

    if base_url and api_key:
        try:
            _embedder = APIEmbedder(base_url, api_key, model)
            logger.info("[embedding] 使用 API embedder: %s @ %s", model, base_url)
            return _embedder
        except Exception as exc:  # noqa: BLE001
            logger.warning("[embedding] API embedder 初始化失败，回退本地：%s", exc)
    elif provider != "local":
        logger.warning(
            "[embedding] provider=%s 但缺少 base_url/api_key，回退本地哈希向量", provider
        )

    dim = int(os.getenv("EMBEDDING_DIM", "256"))
    _embedder = LocalEmbedder(dim=dim)
    logger.info("[embedding] 使用本地哈希 embedder (dim=%d)", dim)
    return _embedder


def embed(text: str) -> List[float]:
    """把文本转成向量（已 L2 归一化）。"""
    return get_embedder().embed(text)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度（入参应为 L2 归一化向量；此处仍做一次保险计算）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))

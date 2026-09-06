"""Small deterministic retrieval test dataset (Phase 3B).

Purpose: verify retrieval behavior during development - NOT the Phase 8
evaluation framework.

Query kinds: keyword (exact surface terms), paraphrase (different wording that
still shares discriminating terms), irrelevant (out-of-corpus domain).

Expected answers are document-level (title + category + optional version).
Since the local deterministic embeddings are lexical, paraphrase cases must
still share discriminating surface terms; this is documented and expected.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.db.enums import KnowledgeCategory


@dataclass(frozen=True)
class RetrievalCase:
    query: str
    kind: str
    expected_title: str | None = None
    expected_category: KnowledgeCategory | None = None
    expected_version: str | None = None
    top_k: int = 5


RETRIEVAL_CASES: list[RetrievalCase] = [
    # --- refund policy -------------------------------------------------
    RetrievalCase("退款政策是什么", "keyword", "退款政策", KnowledgeCategory.REFUND, "2.0.0"),
    RetrievalCase("签收后多少天内可以申请退款", "paraphrase", "退款政策", KnowledgeCategory.REFUND, "2.0.0"),
    RetrievalCase("退款金额怎么算", "keyword", "退款政策", KnowledgeCategory.REFUND, "2.0.0"),
    RetrievalCase("什么情况下不能退款", "paraphrase", "退款政策", KnowledgeCategory.REFUND, "2.0.0"),
    RetrievalCase("退款审核需要多久", "paraphrase", "退款政策", KnowledgeCategory.REFUND, "2.0.0"),
    RetrievalCase("商品漏发了可以退款吗", "paraphrase", "退款政策", KnowledgeCategory.REFUND, "2.0.0"),
    # --- return policy ------------------------------------------------
    RetrievalCase("七天无理由退货条件", "keyword", "退货政策", KnowledgeCategory.RETURN),
    RetrievalCase("退货的运费谁承担", "paraphrase", "退货政策", KnowledgeCategory.RETURN),
    RetrievalCase("退货质检要多久", "paraphrase", "退货政策", KnowledgeCategory.RETURN),
    RetrievalCase("定制商品支持退货吗", "keyword", "退货政策", KnowledgeCategory.RETURN),
    # --- exchange policy ----------------------------------------------
    RetrievalCase("换货政策", "keyword", "换货政策", KnowledgeCategory.EXCHANGE),
    RetrievalCase("质量问题多久内可以换货", "paraphrase", "换货政策", KnowledgeCategory.EXCHANGE),
    RetrievalCase("换货运费规则", "keyword", "换货政策", KnowledgeCategory.EXCHANGE),
    RetrievalCase("换货缺货怎么办", "paraphrase", "换货政策", KnowledgeCategory.EXCHANGE),
    # --- logistics -----------------------------------------------------
    RetrievalCase("发货时效是多久", "keyword", "物流与配送政策", KnowledgeCategory.LOGISTICS),
    RetrievalCase("包裹多久能送到", "paraphrase", "物流与配送政策", KnowledgeCategory.LOGISTICS),
    RetrievalCase("物流异常怎么处理", "keyword", "物流与配送政策", KnowledgeCategory.LOGISTICS),
    RetrievalCase("显示已签收但没收到货", "paraphrase", "物流与配送政策", KnowledgeCategory.LOGISTICS),
    # --- coupon ---------------------------------------------------------
    RetrievalCase("优惠券有效期多久", "keyword", "优惠券政策", KnowledgeCategory.COUPON),
    RetrievalCase("退款后优惠券会退回吗", "paraphrase", "优惠券政策", KnowledgeCategory.COUPON),
    RetrievalCase("优惠券什么时候会退回", "paraphrase", "优惠券政策", KnowledgeCategory.COUPON),
    # --- SOP ------------------------------------------------------------
    RetrievalCase("客服服务流程", "keyword", "客服服务标准作业程序", KnowledgeCategory.SOP),
    RetrievalCase("什么时候需要转人工", "paraphrase", "客服服务标准作业程序", KnowledgeCategory.SOP),
    RetrievalCase("人工交接需要记录什么", "paraphrase", "客服服务标准作业程序", KnowledgeCategory.SOP),
    # --- irrelevant (out-of-corpus domain) ------------------------------
    RetrievalCase("今天的股票行情怎么样", "irrelevant", None, None),
    RetrievalCase("推荐一部好看的电影", "irrelevant", None, None),
    RetrievalCase("基金定投收益率怎么计算", "irrelevant", None, None),
]
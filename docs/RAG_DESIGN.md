# RAG Design — Retrieval · Reranking · Context Assembly

> 本文记录本项目 RAG 知识检索链路的确定设计(Phase 3B / 3C 落地),是
> docs/ARCHITECTURE.md §3 / §15 / §16、docs/DECISIONS.md(010–018)与
> docs/DEVELOPMENT_PLAN.md Phase 3B / 3C 的配套说明。仓库是唯一事实来源。

## 1. 目标链路

```text
Query
 → Query Processing(NFKC / 空白归一 / 空查询校验;无 LLM rewrite)
 → Hybrid Retrieval
     ├── Dense(向量相似度;本地确定性 EmbeddingProvider 桩)
     └── Sparse(Okapi BM25,CJK 滑动二元组)
 → RRF Fusion(k=60)
 → Candidate Set(Top 20,retrieval_top_k)
 → Reranking(DeterministicReranker → Top 5,rerank_top_k)
 → Context Assembly(去重 / ACTIVE 优先 / token budget / citation)
 → Final Context(typed ContextPackage)
 → Grounding Boundary → LLM 生成(后续阶段,不在本链路内)
```

核心思想:**Retrieval 找候选,Reranker 负责相关性排序,Context Assembly
负责决定最终给模型什么。**

## 2. 各环节职责与边界

- **Retrieval(Phase 3B)** 只产生「排序后的候选片段」:typed
  `RetrievalCandidate`(chunk_id / document_id / title / category / version /
  section / content / score / rank / retrieval_methods / metadata),默认只取
  ACTIVE 版本,支持 category / language / status typed 过滤;无候选时返回空
  candidates,不虚构答案。(Decision 010 / 011 / 012 / 013 / 014)
- **Reranking(Phase 3C)** 对候选做相关性重排,不新增、不删除知识:
  - `Reranker` Protocol 为真实 reranker 的替换点;
  - `DeterministicReranker` 是确定性、纯标准库的测试实现,**明确非语义**;
  - 打分 = lexical + title + section + exact-term + retrieval rank +
    method-diversity − per-document duplicate penalty,权重集中在
    `RerankWeights`。(Decision 015 / 018)
- **Context Assembly(Phase 3C)** 决定最终进入模型的上下文:
  - 版本安全去重:chunk_id;同文档同节同内容。不同版本(different versions)
    永不合并、历史不删除;
  - 防御性 ACTIVE 优先:同 (category, title, section) 组内 ACTIVE 优先,
    DRAFT / ARCHIVED 仅在该组无 ACTIVE 时兜底;
  - 按 relevance 顺序整块装入预算,超预算即停止,绝不半截 chunk;
  - 输出 `ContextItem` + `ContextPackage`,完整保留 citation 与溯源。
    (Decision 016)

## 3. Top-K 与 Token Budget

- 两阶段 Top-K 使用 typed `RetrievalPipelineConfig`:
  `retrieval_top_k=20` → `rerank_top_k=5`(校验 rerank_top_k ≤
  retrieval_top_k),参数不散落在代码中。
- Token budget 完全由 context 层控制:`ContextBudget(max_tokens=2000,
  reserve_tokens=0)`;`reserve_tokens` 为未来 system prompt / user prompt /
  answer 预留。
- 无 tokenizer 依赖时,`estimate_tokens()` 是确定性近似(CJK≈1 字/token,
  ASCII≈4 字符/token),**文档注明仅为 approximation**,并非真实 tokenizer
  计数;真实 tokenizer 可后续在同一位置替换。(Decision 017)

## 4. Grounding Boundary

Context Assembly 阶段严格禁止:

- 编造答案 / 自动补充知识;
- 调用业务 API / 查询订单 / 执行退款 / 执行取消订单。

静态知识走 RAG;动态业务事实在后续 Agent Phase 通过 Tools 获取。最终
Context 里有什么,LLM 才允许基于什么知识作答;不满足时只能追问 / 拒答 /
转人工(由后续阶段决定)。

## 5. 溯源 / Citation

每个进入最终上下文的条目都必须能回答「这句话来自哪里」,因此
`ContextItem` 保留:chunk_id、document_id、source_id、title、category、
version、status、section、language、content、relevance_score、
retrieval_methods。检索层 metadata 快照与 FK 双重可查。

## 6. 模块与文档索引

- 代码:backend/app/retrieval/ 下 text / bm25 / dense / fusion / service
  (3B),rerank / context / pipeline(3C)。
- 测试:test_retrieval.py(3B,42 例)、test_reranking.py 与 test_context.py
  (3C,含端到端 retrieval→rerank→context)。
- 决策:docs/DECISIONS.md 010–018。
- 架构:docs/ARCHITECTURE.md §3 / §15 / §16。
- 路线:docs/DEVELOPMENT_PLAN.md Phase 3B / 3C。

## 7. 当前验证状态

- SQLite 内存库 + 确定性 seed:检索链路相关用例(3B 42 例 + 3C rerank/context 用例)全部通过;项目全套测试(含 Phase 4A / 4B)共 273 例全绿,见 README / DEVELOPMENT_PLAN。
- 本地 dense 桩为词面重叠感知的确定性向量,**非语义**;真实语义质量需要
  真实 Embedding 模型 + pgvector(后续阶段)。
- ⚠️ PostgreSQL / pgvector **未实机验证**(本机无 Docker),数据库级向量检索
  与索引性能未测量,不声称已验证。

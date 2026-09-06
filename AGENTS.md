# AGENTS.md

> 本项目仓库的 Codex 开发规范。仓库本身是唯一事实来源(repository is the source of truth)。

## Project Goal

Enterprise AI Customer Service Agent。

最终目标(理解 → 检索 → 决策 → 行动 → 验证 → 升级):

Understand → Retrieve → Decide → Act → Verify → Escalate

## Architecture Principles

1. Static knowledge → RAG
2. Dynamic business data → Tools
3. LangGraph → orchestration/runtime
4. Business logic should not depend heavily on LangGraph
5. MCP → standardized tool/context exposure
6. High-risk operations → Risk Control
7. Sensitive operations → Human-in-the-loop
8. Evaluation and Observability are first-class components

## Development Rules

- Small incremental changes(小步增量修改)
- Inspect existing code before modifying(修改前先检查既有代码)
- Do not rewrite unrelated code(不重写无关代码)
- Do not add unnecessary dependencies(不添加不必要的依赖)
- Do not use deprecated OpenAI Assistants API(不使用已废弃的 OpenAI Assistants API)
- Future model integration should use OpenAI Responses API(未来模型集成使用 OpenAI Responses API)
- Do not fabricate test results(不伪造测试结果)
- Do not fabricate evaluation results(不伪造评测结果)
- Run tests after changes(改动后运行测试)
- Update documentation when architecture changes(架构变更时更新文档)
- Do not automatically move to the next phase(不自动进入下一阶段)

## Context Rules

The repository is the source of truth.

Before each task:

1. Read AGENTS.md
2. Read only relevant documentation
3. Inspect relevant source code
4. Inspect relevant tests

Do NOT load the entire repository into context unless necessary.
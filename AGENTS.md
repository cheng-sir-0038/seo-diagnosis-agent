# Agent 协作指引

本仓库是独立的 SEO 诊断 Agent，包含对话运行、数据查询、审批和前端工作台。

- 修改代码前，先阅读 [README](README.md) 和受影响的现有实现、调用方及测试。
- 开发、修复和验证按 [开发工作流](.agents/workflows/development.md) 处理。
- 涉及诊断、运行恢复或审批时，先核对 [诊断工作流](.agents/workflows/seo-diagnosis.md) 中的实际入口和边界。
- 保持改动范围明确；凭据和运行数据只放在本地，不写入仓库。

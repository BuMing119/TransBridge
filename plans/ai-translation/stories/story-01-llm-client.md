# Story 01: LLM 客户端抽象层

**所属方案**: `plans/ai-translation/plan.md`
**状态**: ✔️ 已实现

## 概述

统一的 LLM 客户端抽象，支持 OpenAI 兼容 API 和 Anthropic API，具备流式输出和连接取消能力。

## 关键设计

- **LLMClient 抽象基类**: chat() + chat_stream() + cancel() 三个核心方法
- **OpenAICompatibleClient**: 适配 OpenAI/DeepSeek/阿里云等 API，stream=True 实现流式
- **AnthropicClient**: 适配 Anthropic Messages API
- **cancel()**: 直接关闭 httpx/requests 底层 HTTP 连接，绕过流式循环的阻塞
- **create_llm_client()**: 工厂函数，根据 LLMConfig.provider 创建对应客户端

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/llm_client.py` | LLMClient 抽象基类 + 两个实现 + 工厂函数 |

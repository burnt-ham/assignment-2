# Step 0: Endpoint Discovery

**Date:** 2026-10-08  
**Status:** Probed and documented

## API Key
All services share the class API key, provided via `CLASS_API_KEY` environment variable.

## Endpoint Details

### 1. Chat Model (9001)
- **Base URL:** `http://dobolyi.com:9001/v1`
- **Model:** `cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit`
- **API:** OpenAI-compatible `/chat/completions`
- **Auth:** Bearer token (the class key, from `CLASS_API_KEY` in your `.env`)
- **Request format:**
  ```json
  {
    "model": "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
  }
  ```
- **Response format:** Standard OpenAI chat completion with `id`, `object`, `created`, `model`, `choices`

### 2. Text Embeddings (9002)
- **Base URL:** `http://dobolyi.com:9002/v1` (without `/v1` the service answers 404 Not Found)
- **Model:** `nvidia/Nemotron-3-Embed-1B-BF16`
- **API:** OpenAI-compatible `/embeddings`
- **Auth:** Bearer token (the class key, from `CLASS_API_KEY` in your `.env`)
- **Request format:**
  ```json
  {
    "model": "nvidia/Nemotron-3-Embed-1B-BF16",
    "input": ["test text"]
  }
  ```
- **Response format:** Standard OpenAI embedding response with `id`, `object`, `data` array containing `index`, `object`, `embedding` (vector)

### 3. Visual Embeddings (9003)
- **Base URL:** `http://dobolyi.com:9003/v1`
- **Model:** `Qwen/Qwen3-VL-Embedding-2B`
- **API:** OpenAI-compatible `/embeddings`
- **Auth:** Bearer token (the class key, from `CLASS_API_KEY` in your `.env`)
- **Request format:**
  ```json
  {
    "model": "Qwen/Qwen3-VL-Embedding-2B",
    "input": [{"type": "text", "text": "test"}]
  }
  ```
- **Note:** Supports multimodal input (text + images)

### 4. Reranker (9004)
- **Base URL:** `http://dobolyi.com:9004`
- **Model:** `Qwen/Qwen3-VL-Reranker-2B`
- **API:** Jina/Cohere-style `/rerank`
- **Auth:** Bearer token (the class key, from `CLASS_API_KEY` in your `.env`)
- **Request format:**
  ```json
  {
    "model": "Qwen/Qwen3-VL-Reranker-2B",
    "query": "test query",
    "documents": ["test document 1", "test document 2"]
  }
  ```
- **Response format:** `id`, `model`, `usage` (prompt_tokens, total_tokens), `results` array with `index`, `document`, `relevance_score`

### 5. Document Parser (9005)
- **Base URL:** `http://dobolyi.com:9005/v1`
- **Model:** `dots.mocr`
- **API:** OpenAI-compatible `/chat/completions`
- **Auth:** Bearer token (the class key, from `CLASS_API_KEY` in your `.env`)
- **Request format:**
  ```json
  {
    "model": "dots.mocr",
    "messages": [{"role": "user", "content": "test"}],
    "max_tokens": 50
  }
  ```
- **Response format:** Standard OpenAI chat completion

## Notes
- All services run on `dobolyi.com` with sequential ports starting at 9001
- Port 9000 is reserved for agentic use (Hermes) — do not use for basic tasks
- Chat completions use standard OpenAI format with `messages` array
- Embeddings use standard OpenAI format with `input` array
- Reranking uses Jina/Cohere-style format with `query` and `documents`
- All endpoints require Bearer token authentication with the shared class key

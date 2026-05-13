# disadvantage_gpt_oss.md

Field notes on running `openai/gpt-oss-20b` under vLLM 0.10.1+gptoss in the PhishingMKT stack (openclaw → vllm → Telegram bot). These are the concrete reasons we patched vllm rather than swapping models or endpoints.

---

## 1. Silent-bot bug on `/v1/responses` — it's a vllm streaming bug, not a model bug

**Symptom.** Under `stream=true`, `/v1/responses` with user-defined function tools ends with `response.completed.response.output == []`. No `message`, no `function_call`, sometimes not even `reasoning`. Openclaw's streaming consumer (see `/app/dist/anthropic-vertex-stream-*.js`) only forwards `message` / `function_call` items to the user, so the Telegram bot appears completely silent.

**Smoking gun — non-streaming works perfectly for the same input.**

```
POST /v1/responses {input:"mấy giờ rồi", tools:[session_status], stream:false}
→ output: ['reasoning', 'function_call']   (5/5 attempts)

POST /v1/responses {input:"mấy giờ rồi", tools:[session_status], stream:true}
→ output: []                                (5/5 attempts)
```

Same prompt, same tools, same seed, same model. The model is correctly emitting the commentary-channel tool call. The bug is in vllm's streaming context / parser, not in the model.

**Root cause located.**

In `serving_responses.py`, streaming uses `StreamingHarmonyContext` (which wraps a `StreamableParser`). Non-streaming uses `HarmonyContext`. At end of generation, both call `_make_response_output_items_with_harmony(context)` which iterates `context.parser.messages`. For `HarmonyContext` this list contains every completed harmony message (reasoning, commentary/tool call, final). For `StreamingHarmonyContext` + `StreamableParser`, the commentary-channel message for a user-defined function tool is not materialised in `parser.messages` by the time generation stops on `<|call|>`, so `output` is empty.

Additionally, the streaming event loop in `responses_stream_generator` only knows how to emit SSE events for built-in tool recipients (`browser.*`, `python`). There is no code path that emits `response.function_call.*` events for recipients under `functions.*`, so even if the parser *did* hold the commentary message, the client would never see any of the incremental `function_call_arguments.delta` events it expects.

**Why "retry with a different seed" does not fix this.** The first approach we took was to buffer the SSE stream, check at end for visible output, and re-run generation with a fresh seed. That did exactly nothing: retries 2 and 3 produced the same `output=[]` as retry 1, because the bug is deterministic in the streaming path — it isn't about which token the model picked, it's about vllm throwing away structured output that the model generated correctly. You can retry a broken parser all day and still get nothing.

**The fix we shipped.** Patch `serving_responses.py` so the streaming endpoint runs generation through `HarmonyContext` (not `StreamingHarmonyContext`) internally, then synthesizes a minimal SSE event sequence from the finished `ResponsesResponse`:

1. `response.created` + `response.in_progress`
2. For each item in `response.output`: `response.output_item.added`, one delta event (`response.output_text.delta` for message text, `response.function_call_arguments.delta` for tool args, `response.reasoning_summary_text.delta` for reasoning text), then `response.output_item.done`.
3. `response.completed` with the full output.

Openclaw's stream consumer treats this sequence as a normal streaming response. Tool calls arrive intact; messages render progressively (one delta per item rather than per-token, but openclaw doesn't care). The trade-off is that the client sees nothing until the whole generation finishes — ~2–4s latency instead of the first token at ~500ms. For Telegram-bot UX that is invisible.

See `patch-harmony.py` patches 5b, 6, 7 against `serving_responses.py`.

**Second bug discovered while shipping the fix: `HarmonyContext.append_output` duplicates messages under streaming generation.**

After forcing the streaming path onto `HarmonyContext` (patch 5b) + `responses_full_generator`, a plain `hi` stream=true produced 19 output items (18 duplicate reasoning + 1 message) where the same request under stream=false returns 2. Root cause is in `vllm/entrypoints/context.py`:

```python
def append_output(self, output) -> None:
    if isinstance(output, RequestOutput):
        for token_id in output.outputs[0].token_ids:
            self.parser.process(token_id)
        output_msgs = self.parser.messages  # <-- cumulative list
    ...
    self._messages.extend(output_msgs)       # <-- re-extended every yield
```

`HarmonyContext` was written assuming `append_output` is called once at the end of generation with the full token list (the non-streaming `responses_full_generator` path). Under streaming — which vllm schedules as one-token-per-yield for harmony — `append_output` is called per yield, and it re-extends `_messages` with the entire (monotonically growing) `parser.messages` list each time. By the end of a 400-token generation, each completed harmony message has been appended dozens of times.

Fix (patch 8 against `context.py`): track a `_parser_msgs_synced` counter and only extend `_messages` with the tail `parser.messages[_parser_msgs_synced:]` on each call. This keeps `_messages` exactly aligned with `parser.messages` at every yield. Verified: `hi` stream=true now returns 2 items; `mấy giờ rồi?` + function tool returns `['reasoning', 'function_call']` 5/5 under streaming, matching non-streaming behaviour.

**Lesson.** The `HarmonyContext` / `StreamingHarmonyContext` split in vllm assumes `HarmonyContext` is never handed streaming engine yields. Routing it that way to dodge the bug in section 1 requires also patching `append_output`, or the cure becomes worse than the disease.

**Things we verified during debugging (leaving the notes here so we don't re-run them):**

- `reasoning.effort = minimal | low | medium` — none of these fix the streaming path because the model is not the problem.
- `temperature = 0` — also irrelevant.
- Stripped system prompt — irrelevant; streaming path still drops the tool call even with a 2-line prompt.
- Removing `<|return|>` / `<|call|>` from `stop_token_ids` — vllm merges in defaults from `stop_tokens_for_assistant_actions` so client overrides are ignored.
- The separate "tutor prints bash instead of calling exec" bug — fixed by rewriting `USER.md` from tutorial tone to directive tone ("You are an agent with an exec tool. Never reply with a code block saying 'here is the command'. Always call exec yourself."). Orthogonal to the streaming bug.

---

## 2. No gpt-oss/harmony tool parser for `/v1/chat/completions`

**What we checked.** `/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/tool_parsers/` ships 18 parsers:

```
deepseekv3, glm4_moe, granite_20b_fc, granite, hermes, hunyuan_a13b,
internlm2, jamba, kimi_k2, llama4_pythonic, llama, minimax, mistral,
phi4mini, pythonic, qwen3coder, step3, xlam
```

Grep across the directory for `gpt_oss|gpt-oss|harmony` returns **zero matches**. There is no parser that knows how to read harmony's `commentary` channel tool-call format out of the chat/completions code path.

**Consequence.** If openclaw is reverted to `openai-completions`, gpt-oss-20b still emits tool calls — but they land as raw JSON text inside `choices[0].message.content`, not as structured `tool_calls[]`. Openclaw looks at `tool_calls`, sees nothing, and tells the user "tôi đã tạo workflow" while actually executing nothing. This was the original bug that drove the switch to `/v1/responses` in the first place.

**Conclusion.** `/v1/chat/completions` is not a viable fallback for this model. We must stay on `/v1/responses` (the harmony-aware path) and fix the silent-bot bug there.

---

## 3. Harmony parser fragility on openclaw-shaped inputs

Before the silent-bot bug surfaced, we had to patch `harmony_utils.py` and `serving_chat.py` (see `patch-harmony.py`) because openclaw sends messages vllm's stock harmony code didn't handle:

- **`content=None` in assistant messages.** `parse_chat_input` assumed `content` is `str` or `list[dict]` and crashed on `None`. Openclaw sends `content: None` for assistant turns that only contain `tool_calls`.
- **Pydantic `ValidatorIterator` on reasoning content.** `parse_response_input` did `assert len(content) == 1`, but pydantic was passing a `ValidatorIterator` whose `len()` raises. Had to materialise to a list.
- **`ToolDescription.new` rejects `None` fields.** Openclaw sometimes omits `description`/`parameters`; stock code forwarded `None` into `ToolDescription.new` which crashed on schema validation.
- **`role="tool"` messages in chat history.** Harmony has no concept of a separate tool role (tool output is embedded in a previous assistant turn). Stock `serving_chat.py` forwarded these and crashed in `parse_chat_input`. We skip them.

These are not model bugs — they're integration gaps between vllm's harmony adapter and OpenAI-spec-compliant clients. Every new vllm release needs these patches re-verified.

---

## 4. `reasoning.effort` is a weak lever

The openclaw openai-provider code only sends `reasoning.effort` when the model has `reasoning: true` in its openclaw.json entry *and* the agent has a non-off thinking level. By default the PhishingMKT vllm entry did not set `reasoning: true`, so openclaw sent no `reasoning` param at all — vllm applied its own default (typically `medium`).

After flipping `reasoning: true` and `thinkingDefault: 'low'`, we verified via the debug log at `/tmp/vllm-requests.log` that openclaw now sends `reasoning.effort=low`. It improves the silent-bot success rate marginally (see table above) but does not eliminate it. Don't rely on it alone.

---

## 5. Large context amplifies the failure

Clean single-turn requests with no history succeed 5/5. The same prompt inside an openclaw conversation that already has 4+ turns of history, a 400-line system prompt (USER.md + openclaw defaults), and 8+ tool definitions fails 2/5. We have not isolated which factor dominates — history length, tool count, or system prompt length — but they all push the same direction.

**Operational implication.** Long-running Telegram conversations degrade over time. Resetting the session (starting a fresh conversation) is a cheap workaround when the bot starts missing replies.

---

## 6. Debug logging at `/tmp/vllm-requests.log`

We patched `serving_responses.py::create_responses` to dump `request.model_dump_json()[:200000]` to `/tmp/vllm-requests.log` at the top of every call. This is how we confirmed what openclaw actually sends (reasoning param, tool list, message history). The log is inside the `vllm` container — tail it with:

```
docker exec vllm tail -f /tmp/vllm-requests.log
```

Keep this patch around while we stabilise gpt-oss behaviour; remove it once the stack is reliable.

---

## 7. Things that are NOT disadvantages worth ripping out for

- **Latency.** gpt-oss-20b on a single GPU at `reasoning=low` answers in 2–4s for short prompts. Fine for Telegram UX.
- **Harmony channels themselves.** The channel design (analysis/commentary/final) is correct for tool-calling — the bug is that the model terminates early, not that the format is wrong.
- **Openclaw's openai-responses adapter.** It's correct; the bug lives below it in the model + vllm serving path.

---

## Summary

The main hazard of gpt-oss-20b on this stack is the intermittent silent-bot bug on `/v1/responses`. `openai/chat/completions` is not a viable fallback because vllm ships no harmony tool parser for that endpoint. The chosen mitigation is a server-side retry patch in `serving_responses.py` that re-samples with a fresh seed when the final output contains no visible message or tool call.

"""
Patch vLLM GPT-OSS harmony mode to handle OpenClaw tool_calls messages
and to retry empty /v1/responses generations (silent-bot bug).

Fixes:
- harmony_utils.py (parse_chat_input): handle content=None in assistant messages
- harmony_utils.py (parse_response_input): handle pydantic ValidatorIterator for reasoning content
- harmony_utils.py (ToolDescription.new): accept None description/parameters
- serving_chat.py: skip role="tool" messages that harmony can't parse
- serving_responses.py: debug request log + retry-on-empty-output for streaming

See docker/vllm/disadvantage_gpt_oss.md for the full reasoning behind these patches.

Usage: python3 patch-harmony.py
"""

import sys

HARMONY_PATH = "/usr/local/lib/python3.12/site-packages/vllm/entrypoints/harmony_utils.py"
SERVING_PATH = "/usr/local/lib/python3.12/site-packages/vllm/entrypoints/openai/serving_chat.py"
RESPONSES_PATH = "/usr/local/lib/python3.12/site-packages/vllm/entrypoints/openai/serving_responses.py"
CONTEXT_PATH = "/usr/local/lib/python3.12/site-packages/vllm/entrypoints/context.py"

# Patch 1: harmony_utils.py - parse_chat_input handles content=None
HARMONY_OLD = '''    if isinstance(content, str):
        contents = [TextContent(text=content)]
    else:
        # TODO: Support refusal.
        contents = [TextContent(text=c["text"]) for c in content]'''

HARMONY_NEW = '''    if content is None:
        contents = [TextContent(text="")]
    elif isinstance(content, str):
        contents = [TextContent(text=content)]
    else:
        # TODO: Support refusal.
        contents = [TextContent(text=c["text"]) for c in content if c is not None and "text" in c]
        if not contents:
            contents = [TextContent(text="")]'''

# Patch 2: harmony_utils.py - parse_response_input reasoning branch
# pydantic passes content as a ValidatorIterator, len() fails. Materialise to list.
HARMONY_REASONING_OLD = '''    elif response_msg["type"] == "reasoning":
        content = response_msg["content"]
        assert len(content) == 1
        msg = Message.from_role_and_content(Role.ASSISTANT, content[0]["text"])'''

HARMONY_REASONING_NEW = '''    elif response_msg["type"] == "reasoning":
        content = list(response_msg["content"])
        first = content[0] if content else {"text": ""}
        text = first["text"] if isinstance(first, dict) else getattr(first, "text", "")
        msg = Message.from_role_and_content(Role.ASSISTANT, text or "")'''

# Patch 2b: harmony_utils.py - ToolDescription.new requires non-None description/parameters
HARMONY_TOOLDESC_OLD = '''                ToolDescription.new(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                ) for tool in function_tools'''

HARMONY_TOOLDESC_NEW = '''                ToolDescription.new(
                    name=tool.name,
                    description=tool.description or "",
                    parameters=tool.parameters or {"type": "object", "properties": {}},
                ) for tool in function_tools'''

# Patch 3: serving_chat.py - skip tool role
SERVING_OLD = '''        # Add user message.
        for chat_msg in request.messages:
            messages.append(parse_chat_input(chat_msg))'''

SERVING_NEW = '''        # Add user message (skip tool role messages that harmony can't handle).
        for chat_msg in request.messages:
            msg_dict = chat_msg if isinstance(chat_msg, dict) else chat_msg.model_dump()
            if msg_dict.get("role") == "tool":
                continue
            messages.append(parse_chat_input(msg_dict))'''

# Patch 4: serving_responses.py - dump every incoming request to /tmp/vllm-requests.log
RESPONSES_DEBUGLOG_OLD = '''    async def create_responses(
        self,
        request: ResponsesRequest,
        raw_request: Optional[Request] = None,
    ) -> Union[AsyncGenerator[str, None], ResponsesResponse, ErrorResponse]:
        error_check_ret = await self._check_model(request)'''

RESPONSES_DEBUGLOG_NEW = '''    async def create_responses(
        self,
        request: ResponsesRequest,
        raw_request: Optional[Request] = None,
    ) -> Union[AsyncGenerator[str, None], ResponsesResponse, ErrorResponse]:
        try:
            import time as _t
            with open('/tmp/vllm-requests.log', 'a') as _f:
                _f.write('--- {} ---\\n'.format(_t.time()))
                _f.write(request.model_dump_json()[:200000])
                _f.write('\\n')
        except Exception:
            pass
        error_check_ret = await self._check_model(request)'''

# Patch 5b: serving_responses.py - always use HarmonyContext (even in streaming).
# StreamingHarmonyContext's parser drops commentary-channel tool calls for
# user-defined function tools, so response.output ends up empty. HarmonyContext
# + responses_full_generator produces the correct output items. We then
# synthesize SSE events from the finished non-streaming response.
RESPONSES_CTX_OLD = '''                    if self.use_harmony:
                        if request.stream:
                            context = StreamingHarmonyContext(
                                messages, tool_sessions)
                        else:
                            context = HarmonyContext(messages, tool_sessions)
                    else:
                        context = SimpleContext()'''

RESPONSES_CTX_NEW = '''                    if self.use_harmony:
                        # Always HarmonyContext. StreamingHarmonyContext loses
                        # commentary-channel tool calls; we run non-stream
                        # internally and synth SSE in responses_stream_synth_generator.
                        context = HarmonyContext(messages, tool_sessions)
                    else:
                        context = SimpleContext()'''

# Patch 6: serving_responses.py - inject _has_visible_harmony_output helper and
# responses_stream_synth_generator before responses_stream_generator.
# The synth generator runs generation non-streaming (so function_call items
# survive) and synthesizes a minimal SSE event sequence from the final
# ResponsesResponse so openclaw's streaming consumer sees real tool calls.
RESPONSES_RETRY_HELPERS_OLD = '''    async def responses_stream_generator(
        self,
        request: ResponsesRequest,
        sampling_params: SamplingParams,
        result_generator: AsyncIterator[Optional[ConversationContext]],
        context: ConversationContext,
        model_name: str,
        tokenizer: AnyTokenizer,
        request_metadata: RequestResponseMetadata,
        created_time: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:'''

RESPONSES_RETRY_HELPERS_NEW = '''    def _has_visible_harmony_output(self, context) -> bool:
        """True if the final harmony output contains at least one user-visible
        item (message or function_call). Used as a retry trigger / diagnostic
        against the silent-bot bug."""
        if not self.use_harmony:
            return True
        try:
            items = self._make_response_output_items_with_harmony(context)
        except Exception:
            return True
        for it in items:
            t = getattr(it, 'type', None)
            if t in ('message', 'function_call'):
                return True
        return False

    async def responses_stream_synth_generator(
        self,
        request,
        sampling_params,
        result_generator,
        context,
        model_name,
        tokenizer,
        request_metadata,
        created_time=None,
    ):
        """Streaming endpoint wrapper that runs generation non-streaming and
        then synthesizes SSE events from the final ResponsesResponse.

        Workaround for a vllm bug: StreamingHarmonyContext's parser drops
        commentary-channel tool calls for user-defined function tools, so the
        final output emitted under stream=true has no function_call items
        even when the model correctly generated one. Running in HarmonyContext
        (non-streaming) and then synthesizing events keeps the client API
        surface intact while making tool calls actually work.
        """
        import json as _synth_json
        import time as _synth_time
        created_time = created_time or int(_synth_time.time())
        _seq = [-1]

        def _emit(event_type, payload):
            _seq[0] += 1
            payload = dict(payload)
            payload["type"] = event_type
            payload["sequence_number"] = _seq[0]
            return "event: {}\\ndata: {}\\n\\n".format(
                event_type, _synth_json.dumps(payload))

        try:
            final = await self.responses_full_generator(
                request,
                sampling_params,
                result_generator,
                context,
                model_name,
                tokenizer,
                request_metadata,
                created_time,
            )
        except Exception as e:
            err = self.create_error_response(str(e))
            yield _emit("error", err.model_dump() if hasattr(err, "model_dump") else {"error": str(e)})
            return

        if isinstance(final, ErrorResponse):
            yield _emit("error", final.model_dump())
            return

        resp_dict = final.model_dump()
        in_progress = dict(resp_dict)
        in_progress["status"] = "in_progress"
        in_progress["output"] = []

        yield _emit("response.created", {"response": in_progress})
        yield _emit("response.in_progress", {"response": in_progress})

        output_items = resp_dict.get("output") or []
        for idx, item in enumerate(output_items):
            itype = item.get("type")
            item_id = item.get("id") or ""
            yield _emit("response.output_item.added", {
                "output_index": idx,
                "item": item,
            })
            if itype == "message":
                content_list = item.get("content") or []
                for cidx, part in enumerate(content_list):
                    if part.get("type") == "output_text":
                        yield _emit("response.output_text.delta", {
                            "output_index": idx,
                            "content_index": cidx,
                            "item_id": item_id,
                            "delta": part.get("text") or "",
                            "logprobs": [],
                        })
            elif itype == "function_call":
                args_str = item.get("arguments") or ""
                if args_str:
                    yield _emit("response.function_call_arguments.delta", {
                        "output_index": idx,
                        "item_id": item_id,
                        "delta": args_str,
                    })
            elif itype == "reasoning":
                for cidx, part in enumerate(item.get("content") or []):
                    if part.get("type") in ("reasoning_text", "summary_text", "output_text"):
                        yield _emit("response.reasoning_summary_text.delta", {
                            "output_index": idx,
                            "content_index": cidx,
                            "item_id": item_id,
                            "delta": part.get("text") or "",
                        })
            yield _emit("response.output_item.done", {
                "output_index": idx,
                "item": item,
            })

        yield _emit("response.completed", {"response": resp_dict})

    async def responses_stream_generator(
        self,
        request: ResponsesRequest,
        sampling_params: SamplingParams,
        result_generator: AsyncIterator[Optional[ConversationContext]],
        context: ConversationContext,
        model_name: str,
        tokenizer: AnyTokenizer,
        request_metadata: RequestResponseMetadata,
        created_time: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:'''

# Patch 7: serving_responses.py - route the streaming branch through the
# synth wrapper instead of responses_stream_generator.
RESPONSES_STREAM_CALL_OLD = '''            if request.stream:
                return self.responses_stream_generator(
                    request,
                    sampling_params,
                    result_generator,
                    context,
                    model_name,
                    tokenizer,
                    request_metadata,
                )'''

RESPONSES_STREAM_CALL_NEW = '''            if request.stream:
                return self.responses_stream_synth_generator(
                    request,
                    sampling_params,
                    result_generator,  # type: ignore[arg-type]
                    context,  # type: ignore[arg-type]
                    model_name,
                    tokenizer,
                    request_metadata,
                )'''

# Patch 8: context.py - HarmonyContext.append_output dedupes parser.messages.
# The stock implementation does self._messages.extend(self.parser.messages) on
# every engine yield. Under streaming generation (1-token-per-yield, as vllm
# schedules for harmony), parser.messages grows monotonically but is appended
# IN FULL every time, so each completed harmony message shows up many times in
# the final _messages. _make_response_output_items_with_harmony then produces
# dozens of duplicate reasoning items.
# Fix: track how many parser.messages we've already synced and only extend
# with the new tail. Tool-output calls still pass through the `else` branch
# as before.
CONTEXT_APPEND_OLD = '''    def append_output(self, output) -> None:
        if isinstance(output, RequestOutput):
            output_token_ids = output.outputs[0].token_ids
            self.parser = get_streamable_parser_for_assistant()
            for token_id in output_token_ids:
                self.parser.process(token_id)
            output_msgs = self.parser.messages
        else:
            # Tool output.
            output_msgs = output
        self._messages.extend(output_msgs)'''

# Patch 9: serving_responses.py - function_call items in request.input never
# get appended to prev_outputs, so a subsequent function_call_output with the
# same call_id can't be matched and parse_response_input raises
# "No call message found for {call_id}" -> 400 Bad Request.
#
# Root cause: request.input is a Union of Pydantic *Param (TypedDict) types
# plus ResponseFunctionToolCall. openclaw sends function_call items as plain
# dicts (TypedDict at runtime), so `isinstance(response_msg,
# ResponseFunctionToolCall)` is always False. prev_outputs is never populated,
# and the tool-result lookup fails on the next iteration of the same input
# list.
#
# Fix: also treat a dict with type == "function_call" as a tool call and
# append a ResponseFunctionToolCall built from it, so the dict form and the
# object form both flow into prev_outputs.
# Patch 10: harmony_utils.py - parse_output_message crashes on commentary
# channel messages with recipient=None.
# The model occasionally emits a commentary-channel turn without specifying a
# recipient (e.g. a self-directed reasoning step formatted as commentary).
# Stock code does `message.recipient.startswith("functions.")` unconditionally
# on the commentary branch → AttributeError: 'NoneType' object has no
# attribute 'startswith' → vllm emits SSE error → openclaw sees "Error Code
# 400: 'NoneType' object has no attribute 'startswith'" and surfaces it.
# Fix: guard the startswith calls with `if message.recipient is not None`.
# commentary with no recipient is treated as a reasoning/analysis item.
HARMONY_COMMENTARY_OLD = '''    elif message.channel == "commentary":
        if message.recipient.startswith("functions."):
            function_name = message.recipient.split(".")[-1]
            for content in message.content:
                random_id = random_uuid()
                response_item = ResponseFunctionToolCall(
                    arguments=content.text,
                    call_id=f"call_{random_id}",
                    type="function_call",
                    name=function_name,
                    id=f"ft_{random_id}",
                )
                output_items.append(response_item)
        elif message.recipient.startswith(
                "python") or message.recipient.startswith("browser"):
            for content in message.content:
                reasoning_item = ResponseReasoningItem(
                    id=f"rs_{random_uuid()}",
                    summary=[],
                    type="reasoning",
                    content=[
                        ResponseReasoningTextContent(text=content.text,
                                                     type="reasoning_text")
                    ],
                    status=None,
                )
                output_items.append(reasoning_item)
        else:
            raise ValueError(f"Unknown recipient: {message.recipient}")'''

HARMONY_COMMENTARY_NEW = '''    elif message.channel == "commentary":
        if message.recipient is not None and message.recipient.startswith(
                "functions."):
            function_name = message.recipient.split(".")[-1]
            for content in message.content:
                random_id = random_uuid()
                response_item = ResponseFunctionToolCall(
                    arguments=content.text,
                    call_id=f"call_{random_id}",
                    type="function_call",
                    name=function_name,
                    id=f"ft_{random_id}",
                )
                output_items.append(response_item)
        elif message.recipient is None or message.recipient.startswith(
                "python") or message.recipient.startswith("browser"):
            for content in message.content:
                reasoning_item = ResponseReasoningItem(
                    type="reasoning",
                    content=[
                        ResponseReasoningTextContent(text=content.text,
                                                     type="reasoning_text")
                    ],
                )
                output_items.append(reasoning_item)
        else:
            raise ValueError(f"Unknown recipient: {message.recipient}")'''

# Patch 11: harmony_utils.py - remove status=None from analysis branch (fixes pydantic error)
HARMONY_ANALYSIS_OLD = '''    elif message.channel == "analysis":
        for content in message.content:
            reasoning_item = ResponseReasoningItem(
                id=f"rs_{random_uuid()}",
                summary=[],
                type="reasoning",
                content=[
                    ResponseReasoningTextContent(text=content.text,
                                                 type="reasoning_text")
                ],
                status=None,
            )
            output_items.append(reasoning_item)'''

HARMONY_ANALYSIS_NEW = '''    elif message.channel == "analysis":
        for content in message.content:
            reasoning_item = ResponseReasoningItem(
                type="reasoning",
                content=[
                    ResponseReasoningTextContent(text=content.text,
                                                 type="reasoning_text")
                ],
            )
            output_items.append(reasoning_item)'''

# Patch 12: harmony_utils.py - remove status=None from remaining state (fixes pydantic error)
HARMONY_REMAINING_OLD = '''    if parser.current_channel == "analysis":
        reasoning_item = ResponseReasoningItem(
            id=f"rs_{random_uuid()}",
            summary=[],
            type="reasoning",
            content=[
                ResponseReasoningTextContent(text=parser.current_content,
                                             type="reasoning_text")
            ],
            status=None,
        )'''

HARMONY_REMAINING_NEW = '''    if parser.current_channel == "analysis":
        reasoning_item = ResponseReasoningItem(
            type="reasoning",
            content=[
                ResponseReasoningTextContent(text=parser.current_content,
                                             type="reasoning_text")
            ],
        )'''

RESPONSES_PREVOUT_OLD = '''            for response_msg in request.input:
                messages.append(
                    parse_response_input(response_msg, prev_outputs))
                # User passes in a a tool call request and its output. We need
                # to add the tool call request to prev_outputs so that the
                # parse_response_input can find the tool call request when
                # parsing the tool call output.
                if isinstance(response_msg, ResponseFunctionToolCall):
                    prev_outputs.append(response_msg)'''

RESPONSES_PREVOUT_NEW = '''            for response_msg in request.input:
                messages.append(
                    parse_response_input(response_msg, prev_outputs))
                if isinstance(response_msg, ResponseFunctionToolCall):
                    prev_outputs.append(response_msg)
                elif isinstance(response_msg, dict) and response_msg.get(
                        "type") == "function_call":
                    try:
                        prev_outputs.append(
                            ResponseFunctionToolCall(
                                type="function_call",
                                call_id=response_msg["call_id"],
                                name=response_msg["name"],
                                arguments=response_msg.get("arguments") or "",
                            ))
                    except Exception:
                        pass'''

CONTEXT_APPEND_NEW = '''    def append_output(self, output) -> None:
        if isinstance(output, RequestOutput):
            output_token_ids = output.outputs[0].token_ids
            for token_id in output_token_ids:
                self.parser.process(token_id)
            parser_msgs = self.parser.messages
            if not hasattr(self, "_parser_msgs_synced"):
                self._parser_msgs_synced = 0
            output_msgs = parser_msgs[self._parser_msgs_synced:]
            self._parser_msgs_synced = len(parser_msgs)
        else:
            # Tool output.
            output_msgs = output
        self._messages.extend(output_msgs)'''


def patch_file(path, old, new, name):
    import os
    if not os.path.exists(path):
        print(f"[SKIP] {name} target {path} not present - nothing to patch")
        return True
    with open(path) as f:
        code = f.read()
    if new in code:
        print(f"[SKIP] {name} already patched")
        return True
    if old not in code:
        print(f"[FAIL] {name} pattern not found - vLLM version may have changed")
        return False
    code = code.replace(old, new)
    with open(path, "w") as f:
        f.write(code)
    print(f"[OK]   {name} patched")
    return True


if __name__ == "__main__":
    r1 = patch_file(HARMONY_PATH, HARMONY_OLD, HARMONY_NEW, "harmony_utils.py (parse_chat_input)")
    r2 = patch_file(HARMONY_PATH, HARMONY_REASONING_OLD, HARMONY_REASONING_NEW, "harmony_utils.py (parse_response_input)")
    r3 = patch_file(HARMONY_PATH, HARMONY_TOOLDESC_OLD, HARMONY_TOOLDESC_NEW, "harmony_utils.py (ToolDescription None)")
    r4 = patch_file(SERVING_PATH, SERVING_OLD, SERVING_NEW, "serving_chat.py")
    r5 = patch_file(RESPONSES_PATH, RESPONSES_DEBUGLOG_OLD, RESPONSES_DEBUGLOG_NEW, "serving_responses.py (debug log)")
    r5b = patch_file(RESPONSES_PATH, RESPONSES_CTX_OLD, RESPONSES_CTX_NEW, "serving_responses.py (HarmonyContext in stream)")
    r6 = patch_file(RESPONSES_PATH, RESPONSES_RETRY_HELPERS_OLD, RESPONSES_RETRY_HELPERS_NEW, "serving_responses.py (synth stream helper)")
    r7 = patch_file(RESPONSES_PATH, RESPONSES_STREAM_CALL_OLD, RESPONSES_STREAM_CALL_NEW, "serving_responses.py (stream synth call)")
    r8 = patch_file(CONTEXT_PATH, CONTEXT_APPEND_OLD, CONTEXT_APPEND_NEW, "context.py (HarmonyContext dedupe)")
    r9 = patch_file(RESPONSES_PATH, RESPONSES_PREVOUT_OLD, RESPONSES_PREVOUT_NEW, "serving_responses.py (dict function_call prev_outputs)")
    r10 = patch_file(HARMONY_PATH, HARMONY_COMMENTARY_OLD, HARMONY_COMMENTARY_NEW, "harmony_utils.py (commentary None recipient)")
    r11 = patch_file(HARMONY_PATH, HARMONY_ANALYSIS_OLD, HARMONY_ANALYSIS_NEW, "harmony_utils.py (analysis remove status)")
    r12 = patch_file(HARMONY_PATH, HARMONY_REMAINING_OLD, HARMONY_REMAINING_NEW, "harmony_utils.py (remaining remove status)")
    sys.exit(0)

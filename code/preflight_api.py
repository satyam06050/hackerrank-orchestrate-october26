"""Verify model access and forced judge-tool output with a synthetic prompt."""

import json
import sys
from typing import Any

import config
from schema import ANTHROPIC_JUDGE_TOOL, ANTHROPIC_JUDGE_TOOL_CHOICE


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return vars(value)
    return value


def _validate_tool_input(value: Any, input_schema: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise ValueError("tool input must be an object")
    properties = input_schema["properties"]
    required = set(input_schema["required"])
    if set(value) != set(properties) or not required.issubset(value):
        raise ValueError("tool input fields do not match the judge schema")
    for name, field_schema in properties.items():
        if field_schema["type"] == "string" and not isinstance(value[name], str):
            raise ValueError(f"tool input field {name!r} must be a string")
        if "enum" in field_schema and value[name] not in field_schema["enum"]:
            raise ValueError(f"tool input field {name!r} is outside its schema enum")


def main() -> int:
    if not config.API_KEY:
        print("Preflight blocked: config.API_KEY is empty; no key was printed.", file=sys.stderr)
        return 2

    try:
        from anthropic import Anthropic
    except ImportError:
        print("Preflight blocked: install the pinned anthropic dependency first.", file=sys.stderr)
        return 2

    try:
        client = Anthropic(api_key=config.API_KEY)
        models = list(client.models.list())
        model_data = [_jsonable(model) for model in models]
        print("MODELS.LIST (FULL)")
        print(json.dumps(model_data, ensure_ascii=False, indent=2, default=str))

        available_ids = {model.get("id") for model in model_data if isinstance(model, dict)}
        missing = [
            model
            for model in (config.PRIMARY_MODEL, config.CHEAP_MODEL)
            if model not in available_ids
        ]
        if missing:
            print(f"Configured models absent from Anthropic model list: {missing}", file=sys.stderr)
            return 3

        response = client.messages.create(
            model=config.PRIMARY_MODEL,
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Synthetic API preflight only; this is not dataset content. "
                        "Judge the inline item 'synthetic-item-001'. Return link='yes', "
                        "rule_id='synthetic-rule', and citation='synthetic inline text'."
                    ),
                }
            ],
            tools=[ANTHROPIC_JUDGE_TOOL],
            tool_choice=ANTHROPIC_JUDGE_TOOL_CHOICE,
        )
    except Exception as error:
        # SDK exception text can include request details; print only the class name.
        print(f"Preflight API call failed: {type(error).__name__}", file=sys.stderr)
        return 1

    print(f"stop_reason: {response.stop_reason}")
    print("usage:")
    print(json.dumps(_jsonable(response.usage), ensure_ascii=False, indent=2, default=str))

    tool_uses = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
    assert len(tool_uses) == 1, f"Expected exactly one tool_use block, got {len(tool_uses)}"
    parsed_input = tool_uses[0].input
    _validate_tool_input(parsed_input, ANTHROPIC_JUDGE_TOOL["input_schema"])
    assert response.stop_reason == "tool_use", f"Expected stop_reason='tool_use', got {response.stop_reason!r}"
    print("parsed tool input:")
    print(json.dumps(parsed_input, ensure_ascii=False, indent=2))
    print("preflight: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

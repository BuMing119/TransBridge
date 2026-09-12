"""Replay synthetic/model captures, or explicitly collect native model routing turns."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from transbridge.smart_assistant.request_evaluation import capture_live, evaluate_corpus

DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / "tests/fixtures/assistant_request_routing/corpus.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replay", type=Path, help="JSON captures; no model/API access")
    mode.add_argument("--live", action="store_true", help="Send synthetic scenario text to the chosen model")
    parser.add_argument("--output", type=Path, help="New live capture file; existing files are never overwritten")
    parser.add_argument("--provider", choices=["openai_compatible", "anthropic"], default="openai_compatible")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", help="Override the OpenAI-compatible endpoint; unavailable for Anthropic")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--require-live", action="store_true", help="Fail unless every live-eligible case has passed")
    args = parser.parse_args(argv)
    if args.live and (not args.output or not args.model or args.max_tokens <= 0):
        parser.error("--live requires --output, --model and positive --max-tokens")
    if args.provider == "anthropic" and args.base_url is not None:
        parser.error("the existing Anthropic client does not support --base-url")
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    captures = json.loads(args.replay.read_text(encoding="utf-8")) if args.replay else []
    evaluate_corpus(corpus, captures)  # Validate local inputs before any provider access.
    if args.live:
        # No desktop configuration or credential store is opened, even in live mode.
        from transbridge.config.llm import LLMConfig
        from transbridge.infra.llm_client import create_llm_client

        key = os.environ.get("TRANSBRIDGE_EVAL_API_KEY", "")
        if not key:
            parser.error("set TRANSBRIDGE_EVAL_API_KEY before opting into --live")
        with args.output.open("x", encoding="utf-8") as stream:
            try:
                client = create_llm_client(
                    LLMConfig(
                        provider=args.provider,
                        api_key=key,
                        base_url=(args.base_url or "https://api.openai.com/v1")
                        if args.provider == "openai_compatible"
                        else "",
                        model=args.model,
                        llm_max_retries=0,
                    )
                )
                for case in corpus["cases"]:
                    if not case.get("replay_only", False):
                        captures.append(
                            capture_live(
                                case, client, model=args.model, provider=args.provider, max_tokens=args.max_tokens
                            )
                        )
            except Exception as error:
                # SDK exception text may include endpoint/request data; retain type only.
                print(json.dumps({"live_capture_error": type(error).__name__, "captured": len(captures)}))
                return 2
            finally:
                json.dump(captures, stream, ensure_ascii=False, indent=2)
    report = evaluate_corpus(corpus, captures)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["counts"].get("failed") or (args.require_live and report["live_acceptance"] != "passed"):
        return 1
    return 0 if captures else 2


if __name__ == "__main__":
    raise SystemExit(main())

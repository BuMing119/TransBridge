import json

from transbridge.application.translation.proofread_log_recovery import recover_proofread_logs


def write_log(root, number, values):
    entries = [
        {
            "entry_key": {"namespace": "plugin", "local_key": str(i)},
            "original": "Hello {name}",
            "current_translation": "你好 {name}",
            "context": "",
            "terms": {},
        }
        for i in range(len(values))
    ]
    messages = [
        {"role": "system", "content": "You proofread translations for a game"},
        {"role": "user", "content": json.dumps({"entries": entries})},
    ]
    response = {
        "results": [
            {"entry_key": entry["entry_key"], "final_translation": value}
            for entry, value in zip(entries, values, strict=True)
        ]
    }
    (root / f"proofread_call_{number:03d}.log").write_text(
        f"[CALL]\n[REQUEST TO LLM]\nmax_tokens=0\n{json.dumps(messages)}\n\n"
        f"[RESPONSE FROM LLM]\n{json.dumps(response)}\n[REQUEST BUDGET] wait_ms=0\n[END CALL]\n",
        encoding="utf-8",
    )


def test_salvage_keeps_only_valid_entries_and_requires_review(tmp_path):
    write_log(tmp_path, 1, ["您好 {name}", "invalid"])
    report = recover_proofread_logs(tmp_path)
    assert report["counts"]["recovered_entries"] == 1
    assert report["counts"]["invalid_entries"] == 1
    assert report["entries"][0]["candidate"] == "您好 {name}"
    assert report["requires_identity_and_terminology_validation"]


def test_repeated_keys_use_latest_valid_evidence_and_numeric_order(tmp_path):
    write_log(tmp_path, 999, ["先前 {name}"])
    write_log(tmp_path, 1000, ["后来 {name}"])
    write_log(tmp_path, 1001, [""])
    report = recover_proofread_logs(tmp_path)
    assert report["counts"]["recovered_entries"] == 1
    assert report["entries"][0]["candidate"] == "后来 {name}"


def test_incomplete_request_is_reported(tmp_path):
    (tmp_path / "proofread_call_001.log").write_text("[CALL]", encoding="utf-8")
    report = recover_proofread_logs(tmp_path)
    assert report["counts"]["unreadable_calls"] == 1
    assert report["entries"] == []

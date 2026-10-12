"""Credential-free run fingerprints still distinguish real business configuration."""

from copy import deepcopy
import json

from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.run_spec import _config_digest, _safe_config_value


def test_token_budgets_participate_in_run_fingerprint():
    config = LLMConfig(max_tokens_per_batch=2000, max_output_tokens=4000)
    changed = deepcopy(config)
    changed.max_tokens_per_batch = 3000
    assert _config_digest(config) != _config_digest(changed)
    changed = deepcopy(config)
    changed.max_output_tokens = 6000
    assert _config_digest(config) != _config_digest(changed)


def test_credential_origins_are_hidden_without_changing_profile_business_values():
    config = LLMConfig(api_key="account-credential", mcp_auth_token="mcp-credential")
    config.embedding.api_key = "embedding-credential"
    business = {"password": "swordfish", "token": "a golden token", "api_key": "a label in the game"}
    config.workflow_profiles = {"password: swordfish": business}
    projected = _safe_config_value(config)

    assert projected["api_key"] is True
    assert projected["mcp_auth_token"] is True
    assert projected["embedding"]["api_key"] is True
    assert projected["workflow_profiles"] == config.workflow_profiles
    assert projected["max_tokens_per_batch"] == 2000
    encoded = json.dumps(projected)
    assert all(value not in encoded for value in ("account-credential", "mcp-credential", "embedding-credential"))
    replacement = deepcopy(config)
    replacement.api_key = "rotated-account-credential"
    assert _config_digest(replacement) == _config_digest(config)


def test_business_profile_changes_affect_digest_even_when_key_looks_sensitive():
    config = LLMConfig(workflow_profiles={"custom": {"token": "one", "password": "first"}})
    changed = deepcopy(config)
    changed.workflow_profiles["custom"]["token"] = "two"
    assert _config_digest(config) != _config_digest(changed)

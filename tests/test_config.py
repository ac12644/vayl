"""
Configuration errors must name the setting and fail at startup — never be silently reinterpreted.
"""
import pytest

from vayl.config import env_float, env_int
from vayl.memory.llm_client import _provider
from vayl.security import kms


def test_numeric_settings_parse_and_default(monkeypatch):
    monkeypatch.setenv("VAYL_T_INT", "42")
    monkeypatch.setenv("VAYL_T_FLOAT", "0.25")
    assert env_int("VAYL_T_INT", 1) == 42
    assert env_float("VAYL_T_FLOAT", 1.0) == 0.25
    monkeypatch.setenv("VAYL_T_INT", "  ")                  # blank means "unset"
    assert env_int("VAYL_T_INT", 7) == 7
    assert env_int("VAYL_T_MISSING", 7) == 7


def test_a_malformed_number_names_the_variable(monkeypatch):
    monkeypatch.setenv("VAYL_PORT", "eighty")
    with pytest.raises(ValueError, match="VAYL_PORT must be an integer, got 'eighty'"):
        env_int("VAYL_PORT", 8080)


@pytest.mark.parametrize("value", ["openia", "ollama"])
def test_an_unknown_llm_provider_is_rejected_not_routed_to_anthropic(monkeypatch, value):
    monkeypatch.setenv("LLM_PROVIDER", value)
    with pytest.raises(ValueError, match="LLM_PROVIDER must be one of"):
        _provider()


def test_known_llm_providers_are_accepted_case_insensitively(monkeypatch):
    for value, expected in [("OpenAI", "openai"), ("anthropic", "anthropic"), (" groq ", "groq")]:
        monkeypatch.setenv("LLM_PROVIDER", value)
        assert _provider() == expected


def test_a_kms_typo_does_not_silently_fall_back_to_a_key_file(monkeypatch, tmp_path):
    monkeypatch.setenv("VAYL_KMS", "vualt")
    with pytest.raises(ValueError, match="VAYL_KMS must be 'file' or 'vault'"):
        kms.data_key(str(tmp_path / "k"))
    assert not (tmp_path / "k").exists()                   # no key file was minted


def test_vault_mode_requires_a_token(monkeypatch, tmp_path):
    monkeypatch.setenv("VAYL_KMS", "vault")
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="requires VAULT_TOKEN"):
        kms.data_key(str(tmp_path / "k"))


def test_env_bool_accepts_the_usual_spellings_and_rejects_typos(monkeypatch):
    from vayl.config import env_bool
    for raw, want in (("on", True), ("TRUE", True), ("1", True), ("yes", True),
                      ("off", False), ("False", False), ("0", False), ("no", False)):
        monkeypatch.setenv("X_FLAG", raw)
        assert env_bool("X_FLAG", not want) is want, raw
    monkeypatch.setenv("X_FLAG", "")
    assert env_bool("X_FLAG", True) is True                   # empty -> default
    monkeypatch.setenv("X_FLAG", "of")
    with pytest.raises(ValueError, match="X_FLAG must be on or off, got 'of'"):
        env_bool("X_FLAG", True)


def test_a_mistyped_security_switch_fails_instead_of_defaulting(monkeypatch, tmp_path):
    """VAYL_ENCRYPT=of used to mean 'on' and VAYL_AUTH_REQUIRED=on used to mean 'off'."""
    from vayl.security import crypto
    monkeypatch.setenv("VAYL_ENCRYPT", "of")
    with pytest.raises(ValueError, match="VAYL_ENCRYPT must be on or off"):
        crypto.resolve(str(tmp_path / "v.db"))


def test_embedder_follows_the_openai_key_and_leaves_explicit_endpoints_alone(monkeypatch):
    from vayl.memory.llm_client import _embed_config
    for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "EMBED_BASE_URL", "EMBED_MODEL", "EMBED_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert _embed_config() == ("http://localhost:11434/v1", "ollama", "nomic-embed-text")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")            # key only: embed where the LLM is
    assert _embed_config() == ("https://api.openai.com/v1", "sk-test", "text-embedding-3-small")
    monkeypatch.setenv("EMBED_BASE_URL", "http://localhost:11434/v1")   # explicit: unchanged
    assert _embed_config()[::2] == ("http://localhost:11434/v1", "nomic-embed-text")
    monkeypatch.delenv("EMBED_BASE_URL")
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://vllm.internal/v1")    # other endpoint: unchanged
    assert _embed_config()[::2] == ("https://vllm.internal/v1", "nomic-embed-text")


def test_cli_help_and_version_answer_without_starting_or_touching_a_db(tmp_path, monkeypatch, capsys):
    """`vayl-mcp --help` used to start the stdio server and create vayl.db in the working directory."""
    import vayl
    from vayl import cli
    monkeypatch.chdir(tmp_path)
    for entry, prog in ((cli.mcp, "vayl-mcp"), (cli.server, "vayl-server")):
        for flag in ("--help", "--version"):
            with pytest.raises(SystemExit) as done:
                entry([flag])
            assert done.value.code == 0
        out = capsys.readouterr().out
        assert f"usage: {prog}" in out and f"{prog} {vayl.__version__}" in out
    assert list(tmp_path.iterdir()) == []


def test_embedder_model_default_matches_the_openai_host_exactly(monkeypatch):
    from vayl.memory.llm_client import _embed_config
    for k in ("OPENAI_API_KEY", "EMBED_BASE_URL", "EMBED_MODEL", "EMBED_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com.proxy.example/v1")
    assert _embed_config()[2] == "nomic-embed-text"          # look-alike host is not OpenAI
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    assert _embed_config()[2] == "text-embedding-3-small"


def test_bad_log_level_and_oidc_role_map_name_their_variable(monkeypatch):
    from vayl.api import mcp_server
    from vayl.auth import sso
    monkeypatch.setenv("VAYL_LOG_LEVEL", "verbose")
    with pytest.raises(ValueError, match="VAYL_LOG_LEVEL must be"):
        mcp_server.configure_logging()
    for k, v in (("VAYL_OIDC_ISSUER", "https://idp"), ("VAYL_OIDC_AUDIENCE", "vayl"),
                 ("VAYL_OIDC_JWKS_URL", "https://idp/jwks")):
        monkeypatch.setenv(k, v)
    for bad in ('{"admins": "admin"', '["admins"]'):          # malformed JSON, not an object
        monkeypatch.setenv("VAYL_OIDC_ROLE_MAP", bad)
        with pytest.raises(ValueError, match="VAYL_OIDC_ROLE_MAP must be a JSON object"):
            sso.configured()


def test_openai_params_follow_the_model_family(monkeypatch):
    # gpt-6 rejects temperature=0, max_tokens and reasoning_effort='minimal'; a live Claude Code run
    # with gpt-6-luna failed every model call before this.
    from vayl.memory.llm_client import _openai_gen_params
    for v in ("OPENAI_MAX_TOKENS", "OPENAI_REASONING_EFFORT", "OPENAI_TEMP"):
        monkeypatch.delenv(v, raising=False)
    assert _openai_gen_params("gpt-6-luna", 400) == {"max_completion_tokens": 2000, "reasoning_effort": "none"}
    assert _openai_gen_params("gpt-6.1-sol", 400)["reasoning_effort"] == "none"
    assert _openai_gen_params("gpt-5-mini", 400)["reasoning_effort"] == "minimal"
    assert _openai_gen_params("o3", 400)["reasoning_effort"] == "minimal"
    assert _openai_gen_params("gpt-4o", 400) == {"max_tokens": 400, "temperature": 0.0}
    assert _openai_gen_params("qwen2.5:3b", 300) == {"max_tokens": 300, "temperature": 0.0}

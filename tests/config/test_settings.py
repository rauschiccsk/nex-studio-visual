from backend.config.settings import Settings


class TestClaudeCliSettings:
    """Test Claude CLI configuration settings."""

    def test_default_claude_config_dir(self):
        s = Settings(_env_file=None)
        assert s.claude_config_dir == "/root/.claude"

    def test_default_claude_cli_path(self):
        s = Settings(_env_file=None)
        assert s.claude_cli_path == "claude"

    def test_default_claude_stream_timeout(self):
        s = Settings(_env_file=None)
        assert s.claude_stream_timeout == 300

    def test_claude_config_dir_from_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/custom/.claude")
        s = Settings(_env_file=None)
        assert s.claude_config_dir == "/custom/.claude"

    def test_claude_cli_path_from_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CLI_PATH", "/usr/local/bin/claude")
        s = Settings(_env_file=None)
        assert s.claude_cli_path == "/usr/local/bin/claude"

    def test_claude_stream_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_STREAM_TIMEOUT", "600")
        s = Settings(_env_file=None)
        assert s.claude_stream_timeout == 600

"""Тесты конфигурации MCP-сервера (``podkop_mcp.config``)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from podkop_mcp.config import Settings, TargetConfig, load_settings
from podkop_mcp.errors import ConfigError


def test_env_single_target_defaults(tmp_path: Path) -> None:
    """Один target из переменных окружения получает разумные значения по умолчанию."""

    env = {
        "PODKOP_MCP_SSH_HOST": "192.0.2.10",
        "PODKOP_MCP_SSH_USER": "admin",
        "PODKOP_MCP_SSH_PORT": "2222",
        "PODKOP_MCP_SSH_KEY_PATH": "~/keys/router",
        "PODKOP_MCP_HOST_KEY_POLICY": "accept-new",
        "PODKOP_MCP_KNOWN_HOSTS": str(tmp_path / "known_hosts"),
    }
    settings = load_settings(env)

    assert list(settings.targets) == ["router"]
    assert settings.default_target == "router"
    target = settings.resolve()
    assert target.host == "192.0.2.10"
    assert target.user == "admin"
    assert target.port == 2222
    assert target.host_key_policy == "accept-new"
    assert target.known_hosts == str(tmp_path / "known_hosts")
    # ~ разворачивается, путь остаётся абсолютным
    assert target.key_path is not None
    assert Path(target.key_path).is_absolute()
    assert target.connect_timeout == 8.0
    assert target.command_timeout == 45.0
    # по умолчанию политика строгая
    assert load_settings({"PODKOP_MCP_SSH_HOST": "h"}).resolve().host_key_policy == "reject"


def test_env_target_name_and_custom_ssh_config_host() -> None:
    """Алиас из ~/.ssh/config допускается без прямого host."""

    settings = load_settings(
        {
            "PODKOP_MCP_SSH_CONFIG_HOST": "router-diag",
            "PODKOP_MCP_TARGET_NAME": "diag",
            "PODKOP_MCP_COMMAND_TIMEOUT": "12.5",
        }
    )
    assert settings.default_target == "diag"
    target = settings.targets["diag"]
    assert target.host is None
    assert target.ssh_config_host == "router-diag"
    assert target.command_timeout == 12.5
    assert target.display_host == "router-diag"


def test_targets_json_multiple_and_default() -> None:
    """PODKOP_MCP_TARGETS задаёт несколько роутеров и default_target."""

    payload = {
        "targets": {
            "home": {"host": "192.0.2.20", "user": "root"},
            "work": {"host": "192.0.2.21", "port": "2022", "backend": "podkop"},
        },
        "default_target": "work",
    }
    settings = load_settings({"PODKOP_MCP_TARGETS": json.dumps(payload)})

    assert set(settings.targets) == {"home", "work"}
    assert settings.default_target == "work"
    assert settings.resolve().host == "192.0.2.21"
    assert settings.resolve().port == 2022
    assert settings.resolve().backend == "podkop"
    assert settings.resolve("home").host == "192.0.2.20"


def test_targets_json_without_wrapper_uses_first() -> None:
    """Допускается «плоский» JSON без обёртки targets."""

    settings = load_settings(
        {"PODKOP_MCP_TARGETS": json.dumps({"r1": {"host": "10.0.0.1"}, "r2": {"host": "10.0.0.2"}})}
    )
    assert settings.default_target == "r1"
    assert settings.targets["r2"].host == "10.0.0.2"


def test_key_b64_valid_and_roundtrip() -> None:
    """key_b64 принимается, декодируется и не утекает открытым текстом."""

    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
    encoded = base64.b64encode(pem.encode("utf-8")).decode("ascii")
    settings = load_settings({"PODKOP_MCP_SSH_HOST": "192.0.2.1", "PODKOP_MCP_SSH_KEY_B64": encoded})
    target = settings.resolve()

    assert target.key_b64 == encoded
    assert base64.b64decode(target.key_b64).decode("utf-8") == pem
    public = target.public_dict()
    assert public["has_inline_key"] is True
    # ключ не попадает в публичное представление ни целиком, ни частями
    serialized = json.dumps(public)
    assert encoded not in serialized
    assert "OPENSSH" not in serialized


def test_inline_key_env_is_encoded_to_b64() -> None:
    """PODKOP_MCP_SSH_KEY (PEM) превращается в key_b64."""

    pem = "-----BEGIN PRIVATE KEY-----\nZZ\n-----END PRIVATE KEY-----\n"
    settings = load_settings({"PODKOP_MCP_SSH_HOST": "192.0.2.1", "PODKOP_MCP_SSH_KEY": pem})
    target = settings.resolve()
    assert target.key_b64 is not None
    assert base64.b64decode(target.key_b64).decode("utf-8") == pem


def test_key_b64_takes_precedence_over_inline_key() -> None:
    """Если заданы оба ключа, побеждает PODKOP_MCP_SSH_KEY_B64, inline игнорируется."""

    pem = "-----BEGIN PRIVATE KEY-----\nZZ\n-----END PRIVATE KEY-----\n"
    encoded = base64.b64encode(b"from-b64").decode("ascii")
    settings = load_settings(
        {
            "PODKOP_MCP_SSH_HOST": "192.0.2.1",
            "PODKOP_MCP_SSH_KEY": pem,
            "PODKOP_MCP_SSH_KEY_B64": encoded,
        }
    )
    target = settings.resolve()

    assert target.key_b64 == encoded
    assert base64.b64decode(target.key_b64) == b"from-b64"


def test_invalid_key_b64_rejected() -> None:
    with pytest.raises(ConfigError, match="key_b64"):
        load_settings(
            {
                "PODKOP_MCP_TARGETS": json.dumps({"r": {"host": "h", "key_b64": "not base64!!"}}),
            }
        )


def test_read_only_and_backup_settings() -> None:
    """Флаги read-only, каталог и число бэкапов читаются из окружения."""

    settings = load_settings(
        {
            "PODKOP_MCP_SSH_HOST": "192.0.2.1",
            "PODKOP_MCP_READ_ONLY": "true",
            "PODKOP_MCP_BACKUP_DIR": "/etc/podkop-mcp/backups",
            "PODKOP_MCP_BACKUP_KEEP": "7",
            "PODKOP_MCP_MAX_OUTPUT_BYTES": "4096",
            "PODKOP_MCP_LOG_LEVEL": "debug",
        }
    )
    assert settings.read_only is True
    assert settings.backup_keep == 7
    assert settings.max_output_bytes == 4096
    assert settings.log_level == "DEBUG"
    assert settings.backup_dir == "/etc/podkop-mcp/backups"

    defaults = load_settings({"PODKOP_MCP_SSH_HOST": "h"})
    assert defaults.read_only is False
    assert defaults.backup_keep == 30
    assert defaults.max_output_bytes == 200_000


def test_empty_environment_is_allowed_but_resolve_fails() -> None:
    """Без настроек сервер стартует, но resolve()/require_targets объясняют причину."""

    settings = load_settings({})
    assert settings.targets == {}
    assert settings.default_target is None
    with pytest.raises(ConfigError, match="не настроен"):
        settings.resolve()
    with pytest.raises(ConfigError, match="не настроен"):
        settings.resolve("nope")


def test_errors_no_host_and_bad_json() -> None:
    with pytest.raises(ConfigError, match="SSH_HOST"):
        TargetConfig(name="x")

    # target из JSON без host и без ssh_config_host тоже отвергается
    with pytest.raises(ConfigError, match="SSH_HOST"):
        load_settings({"PODKOP_MCP_TARGETS": json.dumps({"r": {}})})

    with pytest.raises(ConfigError, match="некорректный JSON"):
        load_settings({"PODKOP_MCP_TARGETS": "{not json"})

    with pytest.raises(ConfigError, match="JSON-объект"):
        load_settings({"PODKOP_MCP_TARGETS": "[1, 2, 3]"})

    with pytest.raises(ConfigError, match="нет ни одного target"):
        load_settings({"PODKOP_MCP_TARGETS": json.dumps({"targets": {}})})


def test_json_target_port_validation() -> None:
    """Порт проверяется и для target из JSON."""

    with pytest.raises(ConfigError, match="порт"):
        load_settings({"PODKOP_MCP_TARGETS": json.dumps({"r": {"host": "h", "port": 70000}})})


@pytest.mark.parametrize("value", ["0", "false", "off", "no", ""])
def test_read_only_disabled_values(value: str) -> None:
    settings = load_settings({"PODKOP_MCP_SSH_HOST": "h", "PODKOP_MCP_READ_ONLY": value})
    assert settings.read_only is False


@pytest.mark.parametrize("value", ["1", "true", "on", " YES "])
def test_read_only_enabled_values(value: str) -> None:
    settings = load_settings({"PODKOP_MCP_SSH_HOST": "h", "PODKOP_MCP_READ_ONLY": value})
    assert settings.read_only is True


def test_errors_default_target_missing_and_unknown_field() -> None:
    with pytest.raises(ConfigError, match="default_target"):
        load_settings(
            {
                "PODKOP_MCP_TARGETS": json.dumps(
                    {"targets": {"a": {"host": "h"}}, "default_target": "b"}
                )
            }
        )

    with pytest.raises(ConfigError, match="неизвестные поля"):
        load_settings({"PODKOP_MCP_TARGETS": json.dumps({"r": {"host": "h", "magic": 1}})})

    with pytest.raises(ConfigError, match="целое число"):
        load_settings({"PODKOP_MCP_SSH_HOST": "h", "PODKOP_MCP_SSH_PORT": "ssh"})


def test_target_validation_port_and_policy() -> None:
    with pytest.raises(ConfigError, match="порт"):
        TargetConfig(name="x", host="h", port=0)
    with pytest.raises(ConfigError, match="host_key_policy"):
        TargetConfig(name="x", host="h", host_key_policy="whatever")  # type: ignore[arg-type]


def test_unknown_target_message_lists_targets() -> None:
    settings = Settings(targets={"a": TargetConfig(name="a", host="h1"), "b": TargetConfig(name="b", host="h2")})
    with pytest.raises(ConfigError, match=r"a, b"):
        settings.resolve("c")


def test_config_file_source(tmp_path: Path) -> None:
    """PODKOP_MCP_CONFIG читается из файла и приоритетнее env-переменных."""

    path = tmp_path / "targets.json"
    path.write_text(
        json.dumps({"targets": {"file": {"host": "10.1.1.1"}}, "default_target": "file"}),
        encoding="utf-8",
    )
    settings = load_settings({"PODKOP_MCP_CONFIG": str(path), "PODKOP_MCP_SSH_HOST": "ignored"})
    assert list(settings.targets) == ["file"]
    assert settings.resolve().host == "10.1.1.1"

    with pytest.raises(ConfigError, match="Не удалось прочитать"):
        load_settings({"PODKOP_MCP_CONFIG": str(tmp_path / "missing.json")})


def test_public_dict_has_no_secrets() -> None:
    """Публичное представление настроек не содержит ключей и паролей."""

    target = TargetConfig(
        name="r",
        host="10.0.0.1",
        key_b64="ZGlyZWN0LXNlY3JldA==",  # base64("direct-secret")
        password="super-secret-password",
        key_passphrase="passphrase-secret",
    )
    settings = Settings(targets={"r": target}, default_target="r")
    serialized = json.dumps(settings.public_dict())
    assert "super-secret-password" not in serialized
    assert "passphrase-secret" not in serialized
    assert "ZGlyZWN0LXNlY3JldA==" not in serialized
    assert settings.public_dict()["targets"]["r"]["has_inline_key"] is True

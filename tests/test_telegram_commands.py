from task_digest.scheduler import _is_allowed_digest_command
from task_digest.telegram import TelegramUpdate


def test_digest_command_is_restricted_to_configured_chat() -> None:
    allowed = TelegramUpdate(update_id=1, chat_id="123", text="/digest")
    wrong_chat = TelegramUpdate(update_id=2, chat_id="456", text="/digest")
    ordinary_message = TelegramUpdate(update_id=3, chat_id="123", text="hello")

    assert _is_allowed_digest_command(allowed, "123") is True
    assert _is_allowed_digest_command(wrong_chat, "123") is False
    assert _is_allowed_digest_command(ordinary_message, "123") is False

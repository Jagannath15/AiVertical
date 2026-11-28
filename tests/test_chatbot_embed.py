from unittest.mock import MagicMock

from scripts.chatbot_embed import embed_texts


def test_embed_texts_uses_injected_model():
    fake_model = MagicMock()
    fake_model.encode.return_value = [[0.1, 0.2], [0.3, 0.4]]
    result = embed_texts(["hello", "world"], model=fake_model)
    assert result == [[0.1, 0.2], [0.3, 0.4]]
    fake_model.encode.assert_called_once_with(["hello", "world"])


def test_embed_texts_empty_list_returns_empty():
    fake_model = MagicMock()
    result = embed_texts([], model=fake_model)
    assert result == []
    fake_model.encode.assert_not_called()

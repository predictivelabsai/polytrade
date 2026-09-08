from polytrade_agent.config import get_settings
from polytrade_agent.storage import encrypted_serializer


def test_checkpoint_serializer_encrypts_reasoning_and_account_data() -> None:
    serializer = encrypted_serializer(get_settings())
    value = {
        "messages": [
            {
                "role": "assistant",
                "reasoning_content": "opaque provider reasoning",
                "content": "public answer",
            },
            {"role": "tool", "content": "private account snapshot"},
        ]
    }

    type_name, ciphertext = serializer.dumps_typed(value)

    assert type_name.endswith("+aes")
    assert b"opaque provider reasoning" not in ciphertext
    assert b"private account snapshot" not in ciphertext
    assert serializer.loads_typed((type_name, ciphertext)) == value

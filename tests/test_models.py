from app.models import DialogTurn, DialogState


def test_dialog_state_add_and_to_messages():
    state = DialogState(call_id="test-call")
    state.add("system", "You are an agent.")
    state.add("user", "Hello")
    msgs = state.to_messages()
    assert len(msgs) == 2
    assert msgs[0] == {"role": "system", "content": "You are an agent."}
    assert msgs[1] == {"role": "user", "content": "Hello"}

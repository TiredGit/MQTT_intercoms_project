import pytest
import state


@pytest.mark.asyncio
async def test_update_doorphones(mocker):
    mock_door_phones = {}
    mocker.patch.object(state, "door_phones", mock_door_phones)

    initial_configs = [
        {"mac": "mac1", "location": "loc1", "allowed_keys": [1, 2], "apartments": [10]},
        {"mac": "mac2", "location": "loc2", "allowed_keys": [3], "apartments": [20]},
    ]

    state.update_doorphones(initial_configs)

    assert len(mock_door_phones) == 2
    assert mock_door_phones["mac1"]["location"] == "loc1"

    # Обновляем — удаляем mac2, добавляем mac3
    new_configs = [
        {"mac": "mac1", "location": "loc1", "allowed_keys": [1, 2], "apartments": [10]},
        {"mac": "mac3", "location": "loc3", "allowed_keys": [4], "apartments": [30]},
    ]

    state.update_doorphones(new_configs)

    assert "mac2" not in mock_door_phones
    assert "mac3" in mock_door_phones


@pytest.mark.asyncio
async def test_call_event(mocker):
    mac = "mac1"
    mock_call_events = {}
    mocker.patch.object(state, "call_events", mock_call_events)

    event = state.call_event(mac)

    assert isinstance(event, dict)
    assert "response_event" in event
    assert "cancel_event" in event

    assert mac in mock_call_events
    assert mock_call_events[mac] == event

    event2 = state.call_event(mac)
    assert event2 is event


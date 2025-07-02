import pytest
from unittest.mock import AsyncMock, MagicMock
from main import send_life, is_valid_config, check_intercom, listen_for_messages, app
import json
import datetime

import yaml
from pathlib import Path

import asyncio

from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_send_life(mocker):
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client

    mocker.patch('main.Client', return_value=mock_client)
    mock_state = mocker.patch('main.state')
    mock_logger = mocker.patch('main.logger')
    mocker.patch('main.asyncio.sleep', side_effect=Exception("stop"))

    mock_state.door_phones = {'mac1': {}, 'mac2': {}}

    class TestDateTime(datetime.datetime):
        @classmethod
        def now(cls):
            return datetime.datetime(2025, 6, 29, 16, 54, 8)

    mocker.patch('main.datetime', TestDateTime)

    with pytest.raises(Exception, match="stop"):
        await send_life()

    assert mock_client.publish.call_count == 2
    for call in mock_client.publish.call_args_list:
        topic = call.args[0]
        if topic == 'intercom/mac2/life':
            payload = call.kwargs.get("payload")
            data = json.loads(payload)
            assert data["time"] == "2025-06-29 16:54:08"
            break
    else:
        pytest.fail("publish не был вызван для mac2")
    mock_logger.info.assert_any_call("Отправка сигнала о работе: mac1")


def test_is_valid_config_true():
    true_data = {"mac": "12", "location": "street", "allowed_keys": [1, 5, 6], "apartments": [15, 20]}
    response = is_valid_config(true_data)
    assert response is True


def test_is_valid_config_false_1():
    false_data = {"mac": "12", "allowed_keys": [1, 5, 6], "apartments": [15, 20]}
    response = is_valid_config(false_data)
    assert response is False


def test_is_valid_config_false_2():
    false_data = {"mac": "12", "location": "street", "allowed_keys": "1, 5, 6", "apartments": [15, 20]}
    response = is_valid_config(false_data)
    assert response is False


@pytest.mark.asyncio
async def test_check_intercom_add(tmp_path, mocker):
    door_dir = tmp_path / "doorphones"
    door_dir.mkdir()
    file_path = door_dir / "12.yml"
    config = {"mac": "12", "location": "street", "allowed_keys": [1, 5, 6], "apartments": [15, 20]}
    file_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    mock_state = mocker.patch('main.state')

    mock_state.previous_configs = {}

    mocker.patch.object(Path, "glob",
                        lambda self, pattern: [file_path] if self == Path("doorphones") else [])

    mock_update = mocker.patch.object(mock_state, "update_doorphones")

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mocker.patch("main.Client", return_value=mock_client)

    mocker.patch("main.asyncio.sleep", side_effect=Exception("stop"))

    with pytest.raises(Exception, match="stop"):
        await check_intercom()

    assert mock_client.publish.call_count == 1

    topic, = mock_client.publish.call_args_list[0].args
    assert topic == "intercom/12/config"

    payload = mock_client.publish.call_args_list[0].kwargs["payload"]
    data = json.loads(payload)
    assert data["event"] == "added"
    assert data["new_config"] == config


@pytest.mark.asyncio
async def test_check_intercom_deletion(tmp_path, mocker):
    door_dir = tmp_path / "doorphones"
    door_dir.mkdir()

    mock_state = mocker.patch('main.state')

    existing_config = {"location": "street", "allowed_keys": [1, 5, 6], "apartments": [15, 20]}
    mock_state.previous_configs = {"12": existing_config}

    mocker.patch.object(Path, "glob", lambda self, pattern: [] if self == Path("doorphones") else [])

    mock_update = mocker.patch.object(mock_state, "update_doorphones")

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mocker.patch("main.Client", return_value=mock_client)

    mocker.patch("main.asyncio.sleep", side_effect=Exception("stop"))

    with pytest.raises(Exception, match="stop"):
        await check_intercom()

    mock_update.assert_called_once_with([])

    # Должно быть ровно 2 вызова publish: один для config-removed, второй для life-deleted
    assert mock_client.publish.call_count == 2

    # Извлечём и проверим оба вызова в порядке их совершения
    calls = mock_client.publish.call_args_list

    # Первый вызов: удаление конфига
    topic1, = calls[0].args
    assert topic1 == "intercom/12/config"
    payload1 = json.loads(calls[0].kwargs["payload"])
    assert payload1["event"] == "removed"
    assert payload1["old_config"] == existing_config

    # Второй вызов: удаление life
    topic2, = calls[1].args
    assert topic2 == "intercom/12/life"
    payload2 = json.loads(calls[1].kwargs["payload"])
    assert payload2["status"] == "deleted"


@pytest.mark.asyncio
async def test_check_intercom_modified(tmp_path, mocker):
    door_dir = tmp_path / "doorphones"
    door_dir.mkdir()
    file_path = door_dir / "12.yml"
    new_config = {"mac": "12", "location": "new_street", "allowed_keys": [2, 3], "apartments": [30, 40]}
    file_path.write_text(yaml.safe_dump(new_config), encoding="utf-8")

    old_config = {"mac": "12", "location": "old_street", "allowed_keys": [1, 5, 6], "apartments": [15, 20]}
    mock_state = mocker.patch('main.state')
    mock_state.previous_configs = {"12": old_config}

    mocker.patch.object(
        Path, "glob",
        lambda self, pattern: [file_path] if self == Path("doorphones") else []
    )

    mock_update = mocker.patch.object(mock_state, "update_doorphones")

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mocker.patch("main.Client", return_value=mock_client)

    mocker.patch("main.asyncio.sleep", side_effect=Exception("stop"))

    with pytest.raises(Exception, match="stop"):
        await check_intercom()

    mock_update.assert_called_once_with([new_config])

    assert mock_client.publish.call_count == 1
    call = mock_client.publish.call_args_list[0]
    topic, = call.args
    assert topic == "intercom/12/config"

    payload = call.kwargs["payload"]
    data = json.loads(payload)
    assert data["event"] == "modified"
    assert data["new_config"] == new_config
    assert data["old_config"] == old_config

    assert mock_state.previous_configs == {"12": new_config}


@pytest.mark.asyncio
async def test_listen_for_messages(mocker):
    mock_message = MagicMock()
    mock_message.topic = "intercom/MAC123/management/door"
    mock_message.payload = json.dumps({"event": "call-response"}).encode()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.messages = AsyncMock()
    mock_client.messages.__aiter__.return_value = [mock_message]

    mocker.patch("main.Client", return_value=mock_client)

    mock_call_event = mocker.patch("main.state.call_event", return_value={"response_event": asyncio.Event()})
    mock_open_door = mocker.patch("main.functions.open_door", new_callable=AsyncMock)
    mock_auto_close = mocker.patch("main.functions.auto_close_door", new_callable=AsyncMock)

    mock_client.subscribe.side_effect = [None, Exception("stop")]

    mocker.patch("main.asyncio.sleep", side_effect=Exception("stop"))

    with pytest.raises(Exception, match="stop"):
        await listen_for_messages()

    assert mock_client.subscribe.call_count == 2
    mock_call_event.assert_called_once_with("MAC123")
    mock_open_door.assert_awaited_once_with("MAC123", management_message="call-response - management-service")


@pytest.mark.asyncio
async def test_root_redirect_no_doorphones(mocker):
    mocker.patch("main.state.get_all_configs", return_value={})
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Нет доступных домофонов"}

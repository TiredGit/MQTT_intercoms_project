import pytest
import json
from unittest.mock import AsyncMock, MagicMock

import functions
import state

from starlette.background import BackgroundTasks
from starlette.requests import Request
from starlette.responses import RedirectResponse

import asyncio


@pytest.mark.asyncio
async def test_open_door_default_branch(mocker):
    fake_doors = {"AA:BB:CC:DD:EE:FF": {"door_status": "closed"},
                  "AA:BB:CC:DD:EE:F2": {"door_status": "closed"},
                  "AA:BB:CC:DD:EE:F3": {"door_status": "closed"}}
    mocker.patch.object(state, "door_phones", fake_doors)
    code = 111
    management_message = "management_message"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mocker.patch("functions.Client", return_value=mock_client)

    await functions.open_door("AA:BB:CC:DD:EE:FF", code=code)
    await functions.open_door("AA:BB:CC:DD:EE:F2", management_message=management_message)
    await functions.open_door("AA:BB:CC:DD:EE:F3")

    assert fake_doors["AA:BB:CC:DD:EE:FF"]["door_status"] == "open"
    assert fake_doors["AA:BB:CC:DD:EE:F2"]["door_status"] == "open"
    assert fake_doors["AA:BB:CC:DD:EE:F3"]["door_status"] == "open"

    assert mock_client.publish.call_count == 3

    expected = [
        ("key", "open"),
        ("management_message", "open"),
        ("call-response", "open"),
    ]

    for call, (exp_event, exp_status) in zip(mock_client.publish.call_args_list, expected):
        payload = json.loads(call.kwargs["payload"])
        assert payload["event"] == exp_event
        assert payload["door_status"] == exp_status


@pytest.mark.asyncio
async def test_auto_close_door(mocker):
    fake_doors = {"AA:BB:CC:DD:EE:FF": {"door_status": "open"}}
    mocker.patch.object(state, "door_phones", fake_doors)

    async def new_sleep(seconds):
        return None
    mocker.patch("functions.asyncio.sleep", new_sleep)

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mocker.patch("functions.Client", return_value=mock_client)

    await functions.auto_close_door("AA:BB:CC:DD:EE:FF")

    assert fake_doors["AA:BB:CC:DD:EE:FF"]["door_status"] == "closed"

    assert mock_client.publish.call_count == 1
    call = mock_client.publish.call_args_list[0]

    topic, = call.args
    assert topic == "intercom/AA:BB:CC:DD:EE:FF/message"

    payload = call.kwargs["payload"]
    data = json.loads(payload)
    assert data["event"] == "auto-close"
    assert data["status"] == "success"
    assert data["door_status"] == "closed"


@pytest.mark.asyncio
async def test_key_valid_code(mocker):
    current_mac = "AA:BB:CC:DD:EE:FF"
    allowed_code = "1234"
    mock_door_phones = mocker.patch.object(state, "door_phones", {})
    mock_door_phones[current_mac] = {
        "door_status": "closed",
        "allowed_keys": [int(allowed_code)]
    }

    mock_request = MagicMock(spec=Request)
    mock_background_tasks = BackgroundTasks()
    mock_open_door = mocker.patch("functions.open_door", new_callable=AsyncMock)
    mock_auto_close = mocker.patch("functions.auto_close_door", new_callable=AsyncMock)

    response = await functions.key(
        request=mock_request,
        background_tasks=mock_background_tasks,
        code=allowed_code,
        current_mac=current_mac,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert response.headers["location"] == f"/{current_mac}"

    mock_open_door.assert_awaited_once_with(current_mac, int(allowed_code))
    assert any(task.func == mock_auto_close and task.args[0] == current_mac for task in mock_background_tasks.tasks)


@pytest.mark.asyncio
async def test_key_invalid_code(mocker):
    current_mac = "AA:BB:CC:DD:EE:FF"
    invalid_code = "0000"
    state.door_phones[current_mac] = {
        "door_status": "closed",
        "allowed_keys": [1234]
    }

    mock_request = MagicMock(spec=Request)
    mock_background_tasks = BackgroundTasks()
    mock_mqtt_client = AsyncMock()
    mock_mqtt_client.__aenter__.return_value = mock_mqtt_client
    mocker.patch("functions.Client", return_value=mock_mqtt_client)

    response = await functions.key(
        request=mock_request,
        background_tasks=mock_background_tasks,
        code=invalid_code,
        current_mac=current_mac,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303

    mock_mqtt_client.publish.assert_awaited_once()
    topic = mock_mqtt_client.publish.call_args.args[0]
    payload = mock_mqtt_client.publish.call_args.kwargs["payload"]

    assert topic == f"intercom/{current_mac}/message"
    assert '"status": "fail"' in payload
    assert '"event": "key"' in payload
    assert '"reason": "incorrect key"' in payload


@pytest.mark.asyncio
async def test_status(mocker):
    mac = "AA:BB:CC:DD:EE:FF"
    fake_door = {"AA:BB:CC:DD:EE:FF": {"door_status": "closed"}}
    mock_door_phones = mocker.patch.object(state, "door_phones", fake_door)
    response = await functions.status(mac)
    assert response["door_status"] == mock_door_phones[mac]["door_status"]


@pytest.mark.asyncio
async def test_call_status(mocker):
    mac = "AA:BB:CC:DD:EE:FF"
    fake_call = {"AA:BB:CC:DD:EE:FF": "calling"}
    mock_call_results = mocker.patch.object(state, "call_results", fake_call)
    response = await functions.call_status(mac)
    assert response["status"] == "calling"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_result, mocked_status, should_pop",
    [
        ({"AA:BB:CC:DD:EE:FF": "waiting"}, {"status": "waiting"}, True),
        ({"AA:BB:CC:DD:EE:FF": "calling"}, {"status": "calling"}, False),
    ]
)
async def test_call_status_update(mocker, initial_result, mocked_status, should_pop):
    mac = "AA:BB:CC:DD:EE:FF"
    mocker.patch.object(state, "call_results", initial_result.copy())

    mock_call_status = mocker.patch.object(
        functions,
        "call_status",
        new_callable=AsyncMock,
        return_value=mocked_status
    )
    result = await functions.call_status_update(mac)

    assert result == mocked_status
    mock_call_status.assert_awaited_once_with(mac)

    if should_pop:
        assert mac not in state.call_results
    else:
        assert state.call_results.get(mac) == initial_result[mac]


@pytest.mark.asyncio
async def test_stop_call(mocker):
    mac = "AA:BB:CC:DD:EE:FF"

    mock_cancel_event = MagicMock()
    mock_event_dict = {"cancel_event": mock_cancel_event}

    mocker.patch.object(state, "call_event", return_value=mock_event_dict)

    response = await functions.stop_call(mac)

    mock_cancel_event.set.assert_called_once()
    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert response.headers["location"] == f"/{mac}"


@pytest.mark.asyncio
@pytest.mark.parametrize("apartments,input_apartment,expect_redirect,template_name", [
    ([101, 102], "999", True, None),
    ([101, 102], "101", False, "call.html"),
])
async def test_call_route(mocker, apartments, input_apartment, expect_redirect, template_name):
    mac = "AA:BB:CC:DD:EE:FF"
    state.call_results.clear()
    state.door_phones[mac] = {
        "location": "Hall",
        "allowed_keys": [],
        "apartments": apartments,
        "door_status": "closed",
    }

    fake_request = MagicMock(spec=Request)
    mock_bg = BackgroundTasks()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mocker.patch("functions.Client", return_value=mock_client)

    mocker.patch("functions.call_wait_response", new_callable=AsyncMock)

    response = await functions.call(
        request=fake_request,
        background_tasks=mock_bg,
        apartment_number=input_apartment,
        current_mac=mac,
    )

    if expect_redirect:
        assert isinstance(response, RedirectResponse)
        assert response.status_code == 303

        mock_client.publish.assert_awaited_once()
        payload = json.loads(mock_client.publish.call_args.kwargs["payload"])
        assert payload["event"] == "call-start"
        assert payload["status"] == "fail"
        assert payload["reason"] == "incorrect apartment"

    else:
        assert response.template.name.endswith("call.html")

        assert state.call_results[mac] == "calling"

        assert any(
            task.func == functions.call_wait_response and task.args[0] == mac
            for task in mock_bg.tasks
        )

        mock_client.publish.assert_awaited_once()
        payload = json.loads(mock_client.publish.call_args.kwargs["payload"])
        assert payload["event"] == "call-start"
        assert payload["status"] == "success"
        assert payload["apartment"] == input_apartment
        assert payload["location"] == "Hall"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "done_key, expected_result",
    [
        ("sleep",    "timeout"),
        ("response", "opened"),
        ("cancel",   "canceled"),
    ],
)
async def test_call_wait_response_variants(monkeypatch, mocker, done_key, expected_result):
    mac = "AA:BB:CC:DD:EE:FF"

    # 1. Подделка событий
    fake_event = {
        "response_event": asyncio.Event(),
        "cancel_event": asyncio.Event(),
    }
    # .wait() теперь просто sync-функция, возвращающая None
    fake_event["response_event"].wait = lambda: None
    fake_event["cancel_event"].wait = lambda: None
    mocker.patch.object(state, "call_event", return_value=fake_event)

    # 2. Устройство зарегистрировано в state
    state.door_phones[mac] = {"door_status": "closed"}

    # 3. Заменяем asyncio.sleep на sync-функцию
    def dummy_sleep(seconds): return None
    monkeypatch.setattr(functions.asyncio, "sleep", dummy_sleep)

    # 4. Класс для фиктивных тасков с cancel()
    class DummyTask:
        def __init__(self, name): self.name = name
        def cancel(self): pass
        def __repr__(self): return f"<DummyTask {self.name}>"

    sleep_task = DummyTask("sleep")
    response_task = DummyTask("response")
    cancel_task = DummyTask("cancel")

    # 5. Подмена create_task
    tasks = [sleep_task, response_task, cancel_task]
    monkeypatch.setattr(functions.asyncio, "create_task", lambda coro: tasks.pop(0))

    # 6. Подмена asyncio.wait
    async def fake_wait(task_set, return_when):
        if done_key == "sleep":
            return {sleep_task}, {response_task, cancel_task}
        elif done_key == "response":
            return {response_task}, {sleep_task, cancel_task}
        else:
            return {cancel_task}, {sleep_task, response_task}
    monkeypatch.setattr(functions.asyncio, "wait", fake_wait)

    # 7. Мокаем MQTT клиента
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mocker.patch("functions.Client", return_value=mock_client)

    # 8. Запуск тестируемой функции
    response = await functions.call_wait_response(mac)

    # 9. Проверки
    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert response.headers["location"] == f"/{mac}"

    assert mac not in state.call_results

    mock_client.publish.assert_awaited_once()
    topic = mock_client.publish.call_args.args[0]
    assert topic == f"intercom/{mac}/message"

    payload = mock_client.publish.call_args.kwargs["payload"]
    data = json.loads(payload)
    assert data["event"] == "call-end"
    assert data["status"] == "success"
    assert data["result"] == expected_result
    assert data["door_status"] == "closed"

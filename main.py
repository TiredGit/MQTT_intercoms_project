import uvicorn
import fastapi
from fastapi import FastAPI, Request, Path
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from typing import Optional

import logging

from contextlib import asynccontextmanager
from aiomqtt import Client
import asyncio

from starlette.responses import RedirectResponse

import functions

import yaml
from pathlib import Path
import state

import json
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task_check = asyncio.create_task(check_intercom())
    task_life = asyncio.create_task(send_life())
    task_message = asyncio.create_task(listen_for_messages())
    yield
    task_check.cancel()
    task_life.cancel()
    task_message.cancel()


async def send_life():
    while True:
        try:
            async with Client("mqtt") as client:
                for mac in state.door_phones.keys():
                    await client.publish(f'intercom/{mac}/life',
                                         payload=json.dumps({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                             "status": "online"}), qos=1)
                    logger.info(f"Отправка сигнала о работе: {mac}")
        except Exception as e:
            logger.error(f"MQTT error: {e}")
        await asyncio.sleep(10)


def is_valid_config(data: dict):
    if not isinstance(data, dict):
        return False
    required_keys = {"mac", "location", "allowed_keys", "apartments"}
    if not required_keys.issubset(data.keys()):
        return False
    if not isinstance(data["mac"], str):
        return False
    if not isinstance(data["location"], str):
        return False
    if not isinstance(data["allowed_keys"], list) or not all(isinstance(k, int) for k in data["allowed_keys"]):
        return False
    if not isinstance(data["apartments"], list) or not all(isinstance(a, int) for a in data["apartments"]):
        return False
    return True


async def check_intercom():
    while True:
        try:
            configs = []
            for path in Path("doorphones").glob("*.yml"):
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if not is_valid_config(data):
                        logger.warning(f"Файл {path.name} имеет неверный формат и будет пропущен")
                        continue
                    configs.append(data)

            new_configs = {cfg["mac"]: cfg for cfg in configs}
            added = set(new_configs) - set(state.previous_configs)
            deleted = set(state.previous_configs) - set(new_configs)
            modified = {mac for mac in new_configs
                        if mac in state.previous_configs and new_configs[mac] != state.previous_configs[mac]}

            if added or deleted or modified:
                state.update_doorphones(configs)

                async with Client("mqtt") as client:
                    for mac in added:
                        await client.publish(f"intercom/{mac}/config", payload=json.dumps({
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "event": "added",
                            "new_config": new_configs[mac]
                        }), qos=1, retain=True)
                        logger.info(f"[MQTT] Подключен домофон: {mac}")

                    for mac in deleted:
                        await client.publish(f"intercom/{mac}/config", payload=json.dumps({
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "event": "removed",
                            "old_config": state.previous_configs[mac]
                        }), qos=1, retain=True)
                        await client.publish(f'intercom/{mac}/life',
                                             payload=json.dumps({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                                 "status": "deleted"}), qos=1)
                        logger.info(f"[MQTT] Удалён домофон: {mac}")

                    for mac in modified:
                        await client.publish(f"intercom/{mac}/config", payload=json.dumps({
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "event": "modified",
                            "new_config": new_configs[mac],
                            "old_config": state.previous_configs[mac]
                        }), qos=1, retain=True)
                        logger.info(f"[MQTT] Изменён домофон: {mac}")

                state.previous_configs = new_configs

            logger.info("Проверка конфигов завершена")
            logger.info(f"{state.get_all_configs()}")

        except Exception as e:
            logger.error(f"Ошибка при загрузке конфигов: {e}")
        await asyncio.sleep(10)


async def listen_for_messages():
    while True:
        try:
            async with Client("mqtt") as client:
                await client.subscribe("intercom/+/management/#")

                async for message in client.messages:
                    try:
                        my_topic = message.topic
                        my_payload = json.loads(message.payload)
                        logger.info(f"New MQTT management message: topic={message.topic}, payload={my_payload}")
                        current_mac = str(my_topic).split("/")[1]

                        sender = 'management-service'
                        event = my_payload.get("event")

                        if event == "call-response":
                            response_event = state.call_event(current_mac)
                            response_event["response_event"].set()
                            logger.info(f"{current_mac} - Получено сообщение от открытии")

                        await functions.open_door(current_mac, management_message=f'{event} - {sender}')
                        _ = asyncio.create_task(functions.auto_close_door(current_mac))

                    except Exception as e:
                        logger.error(f"Ошибка при обработке MQTT-сообщения: {e}")
        except Exception as e:
            logger.error(f"Ошибка при подписке на MQTT: {e}")
            await asyncio.sleep(5)
            logger.info("MQTT: пробуем переподключиться...")


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(functions.router)


@app.get("/")
async def root_redirect():
    door_phones = state.get_all_configs()
    if not door_phones:
        return {"message": "Нет доступных домофонов"}

    first_mac = list(door_phones.keys())[0]
    return RedirectResponse(url=f"/{first_mac}")


@app.get("/{current_mac}")
async def main(request: Request, error_message: Optional[str] = None,
               current_mac: str = fastapi.Path(..., min_length=17, max_length=17)):
    if current_mac not in state.door_phones:
        return RedirectResponse(url=f"/")
    return templates.TemplateResponse(
        request, "main.html", {"door_phones": state.door_phones,
                               "error_message": error_message,
                               "current_mac": current_mac}
    )


if __name__ == '__main__':
    uvicorn.run("main:app", port=8000, reload=True, host='0.0.0.0')

import uvicorn
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager
import paho.mqtt.client as mqtt
import asyncio
import time

# MQTT настройки
MQTT_BROKER = "mqtt"
MQTT_PORT = 1883
MQTT_TOPIC = "intercom/status"

# Глобальный MQTT клиент
mqtt_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Запуск при старте приложения
    global mqtt_client
    mqtt_client = mqtt.Client()
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()

    # Запуск фоновой задачи
    task = asyncio.create_task(send_heartbeat())

    yield

    # Очистка при завершении
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    task.cancel()


async def send_heartbeat():
    """Функция для отправки heartbeat сообщений каждые 10 секунд"""
    while True:
        try:
            mqtt_client.publish(MQTT_TOPIC, "intercom is working", qos=0)
            print(f"Sent MQTT heartbeat at {time.time()}")
        except Exception as e:
            print(f"Error sending MQTT heartbeat: {e}")

        await asyncio.sleep(10)


app = FastAPI()
templates = Jinja2Templates(directory="../templates")
app.mount("/static", StaticFiles(directory="../static"), name="static")


@app.get("/")
async def main(request: Request):
    return templates.TemplateResponse(request, "main.html")


if __name__ == '__main__':
    uvicorn.run("main:app", port=8000, reload=True, host='0.0.0.0')

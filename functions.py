import asyncio
from typing import Optional

from aiomqtt import Client
from fastapi import BackgroundTasks, APIRouter, Form, Request, Path

import logging

from starlette.responses import RedirectResponse
from starlette.templating import Jinja2Templates

import state

import json
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


async def open_door(current_mac: str, code: Optional[int] = None, management_message: Optional[str] = None):
    if state.door_phones[current_mac]['door_status'] == 'closed':
        state.door_phones[current_mac]['door_status'] = 'open'
        logger.info(f'Door status changed: {state.door_phones[current_mac]['door_status']}')

        if code:
            payload = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "event": "key",
                       "key": code,
                       "status": "success",
                       "door_status": state.door_phones[current_mac]['door_status']}
        elif management_message:
            payload = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "event": f"{management_message}",
                       "status": "success",
                       "door_status": state.door_phones[current_mac]['door_status']}
        else:
            payload = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "event": "call-response",
                       "status": "success",
                       "door_status": state.door_phones[current_mac]['door_status']}

        try:
            async with Client("mqtt") as client:
                await client.publish(f'intercom/{current_mac}/message',
                                     payload=json.dumps(payload),
                                     qos=1)
                logger.info(f'{current_mac} - Дверь открыта')
        except Exception as e:
            logger.error(e)


async def auto_close_door(current_mac: str):
    if state.door_phones[current_mac]['door_status'] == 'open':
        await asyncio.sleep(10)
        state.door_phones[current_mac]['door_status'] = 'closed'
        logger.info(f'Door status changed: {state.door_phones[current_mac]['door_status']}')
        try:
            async with Client("mqtt") as client:
                await client.publish(f'intercom/{current_mac}/message',
                                     payload=json.dumps({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                         "event": "auto-close",
                                                         "status": "success",
                                                         "door_status": state.door_phones[current_mac]['door_status']}),
                                     qos=1)
                logger.info(f'{current_mac} - Дверь закрыта')
        except Exception as e:
            logger.error(e)


@router.post('/{current_mac}/open-door-key')
async def key(request: Request, background_tasks: BackgroundTasks, code: str = Form(...),
              current_mac: str = Path(..., min_length=17, max_length=17)):
    if not code.isdigit() or int(code) not in state.door_phones[current_mac]['allowed_keys']:
        try:
            async with Client("mqtt") as client:
                await client.publish(f'intercom/{current_mac}/message',
                                     payload=json.dumps({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                         "event": "key",
                                                         "status": "fail",
                                                         "reason": "incorrect key",
                                                         "door_status": state.door_phones[current_mac]['door_status']}),
                                     qos=1)
                logger.info(f'{current_mac} - Дверь закрыта')
        except Exception as e:
            logger.error(e)
        return RedirectResponse(f"/{current_mac}?error_message=Ключ+не+подходит", status_code=303)
    await open_door(current_mac, int(code))
    background_tasks.add_task(auto_close_door, current_mac)
    return RedirectResponse(f"/{current_mac}", status_code=303)


@router.get("/{current_mac}/status")
async def status(current_mac: str = Path(..., min_length=17, max_length=17)):
    return {"door_status": state.door_phones[current_mac]['door_status']}


@router.get("/{current_mac}/call-status")
async def call_status(current_mac: str = Path(..., min_length=17, max_length=17)):
    call_stat = state.call_results.get(current_mac, "waiting")
    logger.info(f"call-status - {call_stat}")
    return {"status": call_stat}


@router.get("/{current_mac}/call-status-update")
async def call_status_update(current_mac: str = Path(..., min_length=17, max_length=17)):
    new_call_stat = await call_status(current_mac)
    if new_call_stat["status"] != "calling":
        state.call_results.pop(current_mac, None)
    logger.info(f"call-status-update - {new_call_stat}")
    logger.info(f"state.call_results - {state.call_results}")
    return new_call_stat


@router.post("/{current_mac}/stop-call")
async def stop_call(current_mac: str = Path(..., min_length=17, max_length=17)):
    logger.info(f"Отмена звонка {current_mac}")
    stop_event = state.call_event(current_mac)
    stop_event["cancel_event"].set()
    return RedirectResponse(f"/{current_mac}", status_code=303)


async def call_wait_response(current_mac: str):
    new_event = state.call_event(current_mac)

    sleep_task = asyncio.create_task(asyncio.sleep(30))
    response_task = asyncio.create_task(new_event["response_event"].wait())
    cansel_task = asyncio.create_task(new_event["cancel_event"].wait())

    logger.info(f"Ожидание открытия {current_mac}")

    done, pending = await asyncio.wait({sleep_task, response_task, cansel_task}, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()

    if sleep_task in done:
        result = "timeout"
    elif response_task in done:
        result = "opened"
    else:
        result = "canceled"

    logger.info(f"{current_mac} - Результат звонка получен - {result}")
    state.call_results[current_mac] = result

    payload = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "event": "call-end",
               "status": "success",
               "result": result,
               "door_status": state.door_phones[current_mac]['door_status']}

    try:
        async with Client("mqtt") as client:
            await client.publish(f'intercom/{current_mac}/message',
                                 payload=json.dumps(payload),
                                 qos=1)
            logger.info(f'{current_mac} - Отправлено сообщение об результатах звонка')
    except Exception as e:
        logger.error(e)

    state.clear_call_event(current_mac)
    state.call_results.pop(current_mac, None)
    logger.info(f"{current_mac} - Очистка event")

    return RedirectResponse(f"/{current_mac}", status_code=303)


@router.post('/{current_mac}/call')
async def call(request: Request, background_tasks: BackgroundTasks, apartment_number: str = Form(...),
               current_mac: str = Path(..., min_length=17, max_length=17)):
    current_status = state.call_results.get(current_mac, "waiting")
    logger.info(f"current_status - {current_status}")
    if current_status != "calling":
        if not apartment_number.isdigit() or int(apartment_number) not in state.door_phones[current_mac]['apartments']:
            try:
                async with Client("mqtt") as client:
                    await client.publish(f'intercom/{current_mac}/message',
                                         payload=json.dumps({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                             "event": "call-start",
                                                             "apartment": apartment_number,
                                                             "location": state.door_phones[current_mac]['location'],
                                                             "status": "fail",
                                                             "reason": "incorrect apartment",
                                                             "door_status": state.door_phones[current_mac]['door_status']}),
                                         qos=1)
                    logger.info(f'{current_mac} - Неверный номер квартиры')
            except Exception as e:
                logger.error(e)
            return RedirectResponse(f"/{current_mac}?error_message=Неверный+номер+квартиры", status_code=303)
        state.call_results[current_mac] = "calling"
        logger.info(f"current_status - {state.call_results[current_mac]}")
        background_tasks.add_task(call_wait_response, current_mac)
        try:
            async with Client("mqtt") as client:
                await client.publish(f'intercom/{current_mac}/message',
                                     payload=json.dumps({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                         "event": "call-start",
                                                         "apartment": apartment_number,
                                                         "location": state.door_phones[current_mac]['location'],
                                                         "status": "success",
                                                         "door_status": state.door_phones[current_mac]['door_status']}),
                                     qos=1)
                logger.info(f'{current_mac} - Звонок в квартиру {apartment_number}')
        except Exception as e:
            logger.error(e)
        return templates.TemplateResponse(request, "call.html", {
            "apartment_number": apartment_number,
            "current_mac": current_mac
        })
    else:
        return templates.TemplateResponse(
            request, "main.html", {"door_phones": state.door_phones,
                                   "error_message": "Звонок уже идет",
                                   "current_mac": current_mac}
        )


@router.post('/select-doorphone')
async def select_doorphone(new_mac: str = Form(...)):
    return RedirectResponse(f'/{new_mac}', status_code=303)

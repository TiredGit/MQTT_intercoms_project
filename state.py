# state.py

import asyncio

door_phones = {}
previous_configs = {}


def update_doorphones(new_configs: list[dict]):
    existing_macs = set(door_phones.keys())
    new_macs = set(cfg["mac"] for cfg in new_configs)

    # Добавляем новые
    for cfg in new_configs:
        mac = cfg["mac"]
        if mac not in door_phones:
            door_phones[mac] = {
                "location": cfg["location"],
                "allowed_keys": cfg["allowed_keys"],
                "apartments": cfg["apartments"],
                "door_status": "closed"
            }

    # Удаляем отсутствующие
    for mac in existing_macs - new_macs:
        del door_phones[mac]


def get_all_configs():
    return door_phones


call_events = {}
call_results = {}


def call_event(mac: str):
    event = call_events.get(mac)
    if event is None:
        event = {
            "response_event": asyncio.Event(),
            "cancel_event": asyncio.Event()
        }
    call_events[mac] = event
    return event


def clear_call_event(mac: str):
    call_events.pop(mac, None)

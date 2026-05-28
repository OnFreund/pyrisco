import asyncio
from pyrisco import RiscoCloud

risco = RiscoCloud("user@example.com", "password", "1234")


async def on_state(alarm):
    print("State update received")
    for partition_id, partition in alarm.partitions.items():
        print(f"  Partition {partition_id}: armed={partition.armed}, triggered={partition.triggered}")
    for zone_id, zone in alarm.zones.items():
        print(f"  Zone {zone_id} ({zone.name}): triggered={zone.triggered}, bypassed={zone.bypassed}")


async def on_event(events):
    print(f"Received {len(events)} new event(s)")
    for event in events:
        print(f"  [{event.time}] {event.text}")


async def on_error(error):
    print(f"SSE error: {error} (will reconnect automatically)")


async def main():
    await risco.login()
    risco.add_state_handler(on_state)
    risco.add_event_handler(on_event)
    risco.add_error_handler(on_error)
    await risco.subscribe_states()
    await asyncio.Future()  # run forever


asyncio.run(main())

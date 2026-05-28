import asyncio
from pyrisco import RiscoCloud, MaxRetriesError

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


async def _restart():
    """Re-login and re-subscribe after all reconnect attempts are exhausted."""
    await risco.close()
    await risco.login()
    await risco.subscribe_states()


async def on_error(error):
    if isinstance(error, MaxRetriesError):
        # All reconnect attempts exhausted — schedule restart in a new task;
        # calling close() from within the SSE task would deadlock because
        # close() awaits the task itself
        print(f"Gave up reconnecting ({error.last_error}), restarting...")
        asyncio.create_task(_restart())
    else:
        # Transient error — pyrisco will reconnect automatically with backoff
        print(f"SSE error: {error} (reconnecting automatically)")


async def main():
    await risco.login()
    risco.add_state_handler(on_state)
    risco.add_event_handler(on_event)
    risco.add_error_handler(on_error)
    await risco.subscribe_states()
    await asyncio.Future()  # run forever


asyncio.run(main())

# PyRisco

A python interface to Risco alarm systems through [Risco Cloud](https://riscocloud.com/ELAS/WebUI).

## Installation

You can install pyrisco from [PyPI](https://pypi.org/project/pyrisco/):

    pip3 install pyrisco

Python 3.11 and above are supported.


## How to use

### Cloud

#### Push updates via SSE (recommended)

Pyrisco can subscribe to Risco Cloud's Server-Sent Events stream and push state changes to your callbacks as they happen, without polling.

```python
import asyncio
from pyrisco import RiscoCloud

r = RiscoCloud("<username>", "<password>", "<pincode>")

async def on_state(alarm):
    print(alarm.partitions[0].armed)
    print(alarm.zones[0].triggered)

async def on_error(error):
    print(f"SSE error: {error}, reconnecting in 5s...")
    await asyncio.sleep(5)
    await r.login()
    await r.subscribe_states()

async def main():
    await r.login()
    r.add_state_handler(on_state)
    r.add_error_handler(on_error)
    await r.subscribe_states()
    await asyncio.Future()  # run forever

asyncio.run(main())
```

Both `add_state_handler` and `add_error_handler` return a callable that removes the handler when called.

After `subscribe_states()` is started, calls to `get_state()` return the latest cached state without making a network request.

#### Polling

You can also poll for state manually:

```python
import asyncio
from pyrisco import RiscoCloud

async def test_cloud():
    r = RiscoCloud("<username>", "<password>", "<pincode>")

    # you can also pass your own session to login. It will not be closed    
    await r.login()
    alarm = await r.get_state()
    # partitions and zones are zero-based in Cloud
    print(alarm.partitions[0].armed)
    
    events = await r.get_events("2020-06-17T00:00:00Z", 10)
    print(events[0].name)
    
    print(alarm.zones[0].name)
    print(alarm.zones[0].triggered)
    print(alarm.zones[0].bypassed)
    
    # arm partition 0
    await r.partitions[0].arm()
    
    # and disarm it
    await r.partitions[0].disarm()
    
    # Partial arming
    await r.partitions[0].partial_arm()
    
    # Group arming
    await r.partitions[0].group_arm("B")
    # or a zero based index
    await r.partitions[0].group_arm(1)
    
    # Don't forget to close when you're done
    await r.close()

asyncio.run(test_cloud())
```

#### RiscoCloud fallback mode

Pyrisco will instruct RiscoCloud to request updates from your control panel, if there is an issue RiscoCloud will return a 72 error code, if this happens,
* pyrisco will try a second time in fallback mode, which will request the last known state from RiscoCloud.
* A flag named `assumed_control_panel_state` will be set to True on the Alarm object to indicate that the state is assumed, rather than obtained from the panel. **Assumed states could be stale.**


### Local

```python
import asyncio
from pyrisco import RiscoLocal

async def test_local():
    # r = RiscoLocal("<host>", <port>, "<pincode>")
    r = RiscoLocal("<host>", 1000, "<pincode>")

    await r.connect()
    
    # Register handlers
    async def _error(error):
      print(f'Error handler: {error}')
    remove_error = r.add_error_handler(_error)
    async def _event(event):
      print(f'Event handler: {event}')
    remove_event = r.add_event_handler(_event)
    async def _default(command, result, *params):
      print(f'Default handler: {command}, {result}, {params}')
    remove_default = r.add_default_handler(_default)
    async def _zone(zone_id, zone):
      print(f'Zone handler: {zone_id}, {vars(zone)}')
    remove_zone = r.add_zone_handler(_zone)
    async def _partition(partition_id, partition):
      print(f'Partition handler: {partition_id}, {vars(partition)}')
    remove_partition = r.add_partition_handler(_partition)
    
    await r.connect()
    # partitions and zones are one-based in Cloud
    print(r.partitions[1].armed)
    
    
    print(r.zones[1].name)
    print(r.zones[1].triggered)
    print(r.zones[1].bypassed)
    
    # arm partition 1
    await r.partitions[1].arm()
    
    # and disarm it
    await r.partitions[1].disarm()
    
    # Partial arming
    await r.partitions[1].partial_arm()
    
    # Group arming
    await r.partitions[1].group_arm("B")
    # or a zero based index
    await r.partitions[1].group_arm(1)
    
    # Don't forget to close when you're done
    await r.disconnect()

asyncio.run(test_local())
```

## Testing PRs

Every pull request automatically publishes a test build as a GitHub pre-release. You can find the install command in the PR comment posted by the bot, or on the [Releases page](https://github.com/OnFreund/pyrisco/releases) (pre-releases are tagged `pr-{number}`).

**pip:**
```
pip install https://github.com/OnFreund/pyrisco/releases/download/pr-42/pyrisco-0.0.0.dev42-py3-none-any.whl
```

**Home Assistant** — temporarily update your integration's `manifest.json` to use the PEP 508 URL form so HA doesn't overwrite it on restart:
```json
"requirements": ["pyrisco @ https://github.com/OnFreund/pyrisco/releases/download/pr-42/pyrisco-0.0.0.dev42-py3-none-any.whl"]
```
Replace `42` with the actual PR number. Revert to the pinned version (e.g. `pyrisco==0.7.1`) after testing.

The install URL is stable for the lifetime of the PR — new commits to the same PR reuse the same tag and wheel name, so you don't need to update `manifest.json` if more commits are pushed.

The pre-release and comment are deleted automatically when the PR is merged or closed.
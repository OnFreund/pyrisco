# PyRisco

A python interface to Risco alarm systems through [Risco Cloud](https://riscocloud.com/ELAS/WebUI).

## Installation

You can install pyrisco from [PyPI](https://pypi.org/project/pyrisco/):

    pip3 install pyrisco

Python 3.7 and above are supported.


## How to use

### Cloud

#### Cloud From control panel and fallback to Cloud

When requesting RiscoCloud information two modes are available:
- `from_control_panel=True` will request to RiscoCloud to connect to your alarm's control panel directly (via the cloud). This mode allows triggered zones to be updated in real-time.
- `from_control_panel=False` will request to RiscoCloud the latest known information published by your alarm's control panel to RiscoCloud. This mode does not provide real-time triggered zones information but is more reliable in case of temporary network issues.

In case of temporary outage (network or configuration issue) between your alarm's panel and RiscoCloud, if `from_control_panel=True`
is set, an OperationError will be raised after 3 retries.

To avoid raising an OperationError in case of temporary outage, you can set `from_control_panel=True` and use `fallback_to_cloud=True` to fall back to RiscoCloud in case of error.
A property `is_from_control_panel` is set on Alarm object to indicate if the information is coming from the control panel or from the cloud.

#### Cloud with Proxy

Proxy use is supported by passing a `proxy` and `proxy_auth` (optional) parameter to the `RiscoCloud` constructor.
See [aiohttp documentation](https://docs.aiohttp.org/en/stable/client_advanced.html#proxy-support) for more information.

### Examples

See Cloud examples in [examples](examples) folder.

```python
import asyncio
from pyrisco import RiscoCloud

async def test_cloud():
    # from_control_panel=True will request to riscocloud to connect to your alarm's control panel directly (via the cloud)
    # this could not be possible if your alarm has connectivity issues (alarm setup, firewall filtering, alarm's internet connectivity issue)
    r = RiscoCloud("<username>", "<password>", "<pincode>", from_control_panel=True, fallback_to_cloud=True)

    # you can also pass your own session to login. It will not be closed    
    await r.login()
    alarm = await r.get_state()
    # partitions and zones are zero-based in Cloud
    print(alarm.partitions[0].armed)
    
    events = await r.get_events("2020-01-01T00:00:00Z", 10)
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
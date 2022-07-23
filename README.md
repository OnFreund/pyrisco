# PyRisco

A python interface to Risco alarm systems through [Risco Cloud](https://riscocloud.com/ELAS/WebUI).

## Installation

You can install pyrisco from [PyPI](https://pypi.org/project/pyrisco/):

    pip3 install pyrisco

Python 3.7 and above are supported.


## How to use

### Cloud
```python
from pyrisco import RiscoCloud
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
```

### Local
```python
from pyrisco import RiscoLocal
r = RiscoLocal("<host>", <port>, "<pincode>")

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
```
# PyRisco

A python interface to Risco alarm systems through [Risco CLoud](https://riscocloud.com/ELAS/WebUI).

## Installation

You can install pyrisco from [PyPI](https://pypi.org/project/pyrisco/):

    pip3 install pyrisco

Python 3.7 and above are supported.


## How to use

```python
from pyrisco import RiscoAPI
r = RiscoAPI("<username>", "<password>", "<pincode>")

# you can also pass your own session to login. It will not be closed
await r.login()
alarm = await r.get_state()
print(alarm.partitions[0].armed)

events = await r.get_events("2020-06-17T00:00:00Z", 10)
print(events[0].name)

print(alarm.zones[0].name)
print(alarm.zones[0].triggered)
print(alarm.zones[0].bypassed)

# arm partition 0
await r.arm(0)

# and disarm it
await r.disarm(0) 

# Partial arming
await r.partial_arm(0)

# Group arming
await r.group_arm(0, "B")
# or a zero based index
await r.group_arm(0, 1)

# Don't forget to close when you're done
await r.close()
```
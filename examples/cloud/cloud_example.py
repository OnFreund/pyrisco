import asyncio

from datetime import datetime, timedelta
from pyrisco import RiscoCloud
from tabulate import tabulate


class RiscoCloudExample:

  def __init__(self, username, password, pin):
    self.risco_cloud = RiscoCloud(username, password, pin)

  def tabulate(self, dataset):
    if len(dataset) == 0:
      print("No data")
      return
    dataset = list(dataset)
    headers = dataset[0]._raw.keys()
    rows = [x._raw.values() for x in dataset]
    print(tabulate(rows, headers=headers))

  async def login(self):
    return await self.risco_cloud.login()

  async def close(self):
    return await self.risco_cloud.close()

  async def display_state(self):
    await self.login()
    state = await self.risco_cloud.get_state()
    print('==================== Alarm status ====================')
    print(f'assumed_control_panel_state: {state.assumed_control_panel_state}')
    print('==================== Partitions ====================')
    self.tabulate(state.partitions.values())
    print('==================== Zones ====================')
    self.tabulate(state.zones.values())
    await self.close()

  async def display_events(self, start_date, limit):
    await self.login()
    events = await self.risco_cloud.get_events(start_date.strftime('%Y-%m-%d'), limit)
    print('==================== Events ====================')
    self.tabulate(events)
    await self.close()

  async def test(self):
    """Example from README.md"""
    await self.login()
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
    await self.close()


# username, password, pin from Risco Cloud ( https://www.riscocloud.com/ )
r = RiscoCloudExample("username", "password", "pin")
asyncio.run(r.display_state())
asyncio.run(r.display_events(datetime.now() - timedelta(days=1), 100))
# commented out because it will arm and disarm your alarm
# asyncio.run(r.test())

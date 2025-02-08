import asyncio

from pyrisco import RiscoCloud
from tabulate import tabulate
from datetime import datetime


class RiscoCloudCheckZonesLoopExample:
  """
  Example of checking triggered zones in a loop.
  This example will work if you can connect to your control panel.
  """

  def __init__(self, username, password, pin, interval=10):
    self.risco_cloud = RiscoCloud(username, password, pin)
    self._sensor_check_interval = interval  # seconds

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

  async def check_zones(self):
    await self.login()
    try:
      while True:
        state = await self.risco_cloud.get_state()
        triggered = [zone.id for zone in state.zones.values() if zone.triggered]
        if triggered:
          print('\n')
          self.tabulate([state.zones[zone] for zone in triggered])
        else:
          print("\r", end="")
        print('{}, Alarm.assumed_control_panel_state: {} , number of zones: {}'.format(
          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          state.assumed_control_panel_state,
          len(state.zones.values())
        ), end="")
        await asyncio.sleep(self._sensor_check_interval)
    finally:
      await self.close()


# username, password, pin from Risco Cloud ( https://www.riscocloud.com/ )
r = RiscoCloudCheckZonesLoopExample("username", "password", "pin")
asyncio.run(r.check_zones())

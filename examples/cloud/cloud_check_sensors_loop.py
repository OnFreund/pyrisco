import asyncio
from pyrisco import RiscoCloud
from tabulate import tabulate
from datetime import datetime, timedelta


class RiscoCloudCheckZonesLoopExample:
    def __init__(self, username, password, pin, interval=10, from_control_panel=True):
        self.username = username
        self.password = password
        self.pin = pin
        self.from_control_panel = from_control_panel
        self.risco_cloud = RiscoCloud(self.username, self.password, self.pin, from_control_panel=self.from_control_panel)
        self.sensor_check_interval = interval  # seconds
        self.zones = []

    async def login(self):
        return await self.risco_cloud.login()

    async def close(self):
        return await self.risco_cloud.close()

    def tabulate(self, dataset):
        if (len(dataset) == 0):
            print("No data")
            return
        dataset = list(dataset)
        headers = dataset[0]._raw.keys()
        rows = [x._raw.values() for x in dataset]
        print(tabulate(rows, headers=headers))

    async def check_zones(self):
        await self.login()
        try:
            while True:
                state = await self.risco_cloud.get_state()
                triggered = [zone.zoneID for zone in state.zones.values() if zone.triggered]
                if triggered:
                    print('\n')
                    self.tabulate([state.zones[zone] for zone in triggered])
                else:
                    print("\r", end="")
                print('{}, number of zones: {}'.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(state.zones.values())), end="")
                await asyncio.sleep(self.sensor_check_interval)
        finally:
            await self.close()


# username, password, pin from Risco Cloud ( https://www.riscocloud.com/ )
r = RiscoCloudCheckZonesLoopExample("username", "password", "pin", from_control_panel=True)
asyncio.run(r.check_zones())

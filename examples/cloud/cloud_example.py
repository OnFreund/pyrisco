import asyncio
from pyrisco import RiscoCloud
from tabulate import tabulate
from datetime import datetime, timedelta


class RiscoCloudExample:
    def __init__(self, username, password, pin, from_control_panel=True):
        self.username = username
        self.password = password
        self.pin = pin
        self.from_control_panel = from_control_panel
        self.risco_cloud = RiscoCloud(self.username, self.password, self.pin, from_control_panel=self.from_control_panel)

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

    async def display_state(self):
        await self.login()
        state = await self.risco_cloud.get_state()
        print('==================== Alarm status ====================')
        ALARM_PROPERTIES = ['cp_time', 'system_status', 'system_ready', 'trouble', 'bell_status', 'bell_on', 'alarm_pending', 'battery_low', 'ac_lost', 'is_online', 'last_connected_time', 'last_log_update', 'last_status_update', 'last_event_reported']
        for prop in ALARM_PROPERTIES:
            print(f'{prop}: {getattr(state, prop)}')
        print('==================== Partitions ====================')
        self.tabulate(state.partitions.values())
        print('==================== Zones ====================')
        self.tabulate(state.zones.values())
        print('==================== Device collections ====================')
        for device_type, collection in state.dev_collections.items():
            print(f'Collection Type: {device_type}')
            self.tabulate(collection.devices.values())
        print('==================== Users ====================')
        self.tabulate(state.users.values())
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
r = RiscoCloudExample("username", "password", "pin", from_control_panel=True)
asyncio.run(r.display_state())
asyncio.run(r.display_events(datetime.now() - timedelta(days=1), 100))

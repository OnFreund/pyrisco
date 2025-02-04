from pyrisco import RiscoCloud
from tabulate import tabulate


class RiscoCloudBaseExample:
  def __init__(self, username, password, pin,
               from_control_panel=True, fallback_to_cloud=True,
               proxy=None, proxy_auth=None):
    self.risco_cloud = RiscoCloud(
      username, password, pin,
      proxy=proxy,
      proxy_auth=proxy_auth,
      from_control_panel=from_control_panel,
      fallback_to_cloud=fallback_to_cloud)

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

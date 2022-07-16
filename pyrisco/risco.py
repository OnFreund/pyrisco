from pyrisco.local.risco_panel import RiscoPanel
from pyrisco.cloud.risco_api import RiscoAPI


def get_risco_cloud(username, password, pin, language="en"):
    return RiscoAPI(username, password, pin, language)

def get_risco_local(host, port, code, **kwargs):
    return RiscoPanel(host, port, code, **kwargs)

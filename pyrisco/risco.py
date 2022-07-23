from pyrisco.local import RiscoPanel
from pyrisco.cloud import RiscoAPI


def get_risco_cloud(username, password, pin, language="en"):
    return RiscoAPI(username, password, pin, language)

def get_risco_local(host, port, code, **kwargs):
    return RiscoPanel(host, port, code, **kwargs)

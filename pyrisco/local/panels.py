from .const import PANEL_TYPE, PANEL_MODEL, PANEL_FW, MAX_ZONES, MAX_PARTS, MAX_OUTPUTS

def _rw032_capabilities(firmware):
  return {
    PANEL_MODEL: 'Agility 4',
    MAX_ZONES: 32,
    MAX_PARTS: 3,
    MAX_OUTPUTS: 4,
  }

def _rw132_capabilities(firmware):
  return {
    PANEL_MODEL: 'Agility',
    MAX_ZONES: 36,
    MAX_PARTS: 3,
    MAX_OUTPUTS: 4,
  }

def _rw232_capabilities(firmware):
  return {
    PANEL_MODEL: 'WiComm',
    MAX_ZONES: 36,
    MAX_PARTS: 3,
    MAX_OUTPUTS: 4,
  }

def _rw332_capabilities(firmware):
  return {
    PANEL_MODEL: 'WiCommPro',
    MAX_ZONES: 36,
    MAX_PARTS: 3,
    MAX_OUTPUTS: 4,
  }

def _rp432_capabilities(firmware):
  max_zones = 32
  max_outputs = 14
  parts = firmware.split('.')
  if int(parts[0]) >= 3:
    max_zones = 50
    max_outputs = 32

  return {
    PANEL_MODEL: 'LightSys',
    MAX_ZONES: max_zones,
    MAX_PARTS: 4,
    MAX_OUTPUTS: max_outputs,
  }

def _rp432mp_capabilities(firmware):
  return {
    PANEL_MODEL: 'LightSys+',
    MAX_ZONES: 512,
    MAX_PARTS: 32,
    MAX_OUTPUTS: 196,
  }


def _rp512_capabilities(firmware):
  max_zones = 64
  parts = list(map(int, firmware.split('.')))
  if ((parts[0] > 1) or
  (parts[0] == 1 and parts[1] > 2) or
  (parts[0] == 1 and parts[1] == 2 and parts[2] > 0) or 
  (parts[0] == 1 and parts[1] == 2 and parts[2] == 0 and parts[3] >= 7)):
    max_zones = 128;

  return {
    PANEL_MODEL: 'ProsysPlus|GTPlus',
    MAX_ZONES: max_zones,
    MAX_PARTS: 32,
    MAX_OUTPUTS: 262,
  };

PANELS = {
  'RW032': _rw032_capabilities,
  'RW132': _rw132_capabilities,
  'RW232': _rw232_capabilities,
  'RW332': _rw332_capabilities,
  'RP432': _rp432_capabilities,
  'RP432MP': _rp432mp_capabilities,
  'RP512': _rp512_capabilities
}

def panel_capabilities(panel_type, firmware):
  normalized = panel_type.split(":")[0]
  firmware = firmware.split(" ")[0]
  caps = PANELS[normalized](firmware)
  return {**caps, **{PANEL_TYPE: panel_type, PANEL_FW: firmware}}

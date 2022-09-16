import base64

CRC_ARRAY_BASE64 = 'WzAsNDkzNDUsNDk1MzcsMzIwLDQ5OTIxLDk2MCw2NDAsNDk3MjksNTA2ODksMTcyOCwxOTIwLDUxMDA5LDEyODAsNTA2MjUsNTAzMDUsMTA4OCw1MjIyNSwzMjY0LDM0NTYsNTI1NDUsMzg0MCw1MzE4NSw1Mjg2NSwzNjQ4LDI1NjAsNTE5MDUsNTIwOTcsMjg4MCw1MTQ1NywyNDk2LDIxNzYsNTEyNjUsNTUyOTcsNjMzNiw2NTI4LDU1NjE3LDY5MTIsNTYyNTcsNTU5MzcsNjcyMCw3NjgwLDU3MDI1LDU3MjE3LDgwMDAsNTY1NzcsNzYxNiw3Mjk2LDU2Mzg1LDUxMjAsNTQ0NjUsNTQ2NTcsNTQ0MCw1NTA0MSw2MDgwLDU3NjAsNTQ4NDksNTM3NjEsNDgwMCw0OTkyLDU0MDgxLDQzNTIsNTM2OTcsNTMzNzcsNDE2MCw2MTQ0MSwxMjQ4MCwxMjY3Miw2MTc2MSwxMzA1Niw2MjQwMSw2MjA4MSwxMjg2NCwxMzgyNCw2MzE2OSw2MzM2MSwxNDE0NCw2MjcyMSwxMzc2MCwxMzQ0MCw2MjUyOSwxNTM2MCw2NDcwNSw2NDg5NywxNTY4MCw2NTI4MSwxNjMyMCwxNjAwMCw2NTA4OSw2NDAwMSwxNTA0MCwxNTIzMiw2NDMyMSwxNDU5Miw2MzkzNyw2MzYxNywxNDQwMCwxMDI0MCw1OTU4NSw1OTc3NywxMDU2MCw2MDE2MSwxMTIwMCwxMDg4MCw1OTk2OSw2MDkyOSwxMTk2OCwxMjE2MCw2MTI0OSwxMTUyMCw2MDg2NSw2MDU0NSwxMTMyOCw1ODM2OSw5NDA4LDk2MDAsNTg2ODksOTk4NCw1OTMyOSw1OTAwOSw5NzkyLDg3MDQsNTgwNDksNTgyNDEsOTAyNCw1NzYwMSw4NjQwLDgzMjAsNTc0MDksNDA5NjEsMjQ3NjgsMjQ5NjAsNDEyODEsMjUzNDQsNDE5MjEsNDE2MDEsMjUxNTIsMjYxMTIsNDI2ODksNDI4ODEsMjY0MzIsNDIyNDEsMjYwNDgsMjU3MjgsNDIwNDksMjc2NDgsNDQyMjUsNDQ0MTcsMjc5NjgsNDQ4MDEsMjg2MDgsMjgyODgsNDQ2MDksNDM1MjEsMjczMjgsMjc1MjAsNDM4NDEsMjY4ODAsNDM0NTcsNDMxMzcsMjY2ODgsMzA3MjAsNDcyOTcsNDc0ODksMzEwNDAsNDc4NzMsMzE2ODAsMzEzNjAsNDc2ODEsNDg2NDEsMzI0NDgsMzI2NDAsNDg5NjEsMzIwMDAsNDg1NzcsNDgyNTcsMzE4MDgsNDYwODEsMjk4ODgsMzAwODAsNDY0MDEsMzA0NjQsNDcwNDEsNDY3MjEsMzAyNzIsMjkxODQsNDU3NjEsNDU5NTMsMjk1MDQsNDUzMTMsMjkxMjAsMjg4MDAsNDUxMjEsMjA0ODAsMzcwNTcsMzcyNDksMjA4MDAsMzc2MzMsMjE0NDAsMjExMjAsMzc0NDEsMzg0MDEsMjIyMDgsMjI0MDAsMzg3MjEsMjE3NjAsMzgzMzcsMzgwMTcsMjE1NjgsMzk5MzcsMjM3NDQsMjM5MzYsNDAyNTcsMjQzMjAsNDA4OTcsNDA1NzcsMjQxMjgsMjMwNDAsMzk2MTcsMzk4MDksMjMzNjAsMzkxNjksMjI5NzYsMjI2NTYsMzg5NzcsMzQ4MTcsMTg2MjQsMTg4MTYsMzUxMzcsMTkyMDAsMzU3NzcsMzU0NTcsMTkwMDgsMTk5NjgsMzY1NDUsMzY3MzcsMjAyODgsMzYwOTcsMTk5MDQsMTk1ODQsMzU5MDUsMTc0MDgsMzM5ODUsMzQxNzcsMTc3MjgsMzQ1NjEsMTgzNjgsMTgwNDgsMzQzNjksMzMyODEsMTcwODgsMTcyODAsMzM2MDEsMTY2NDAsMzMyMTcsMzI4OTcsMTY0NDhd'
ENCRYPTION_FLAG_INDEX = 1
ENCRYPTION_FLAG_VALUE = 17

START = b'\x02'
END = b'\x03'
DLE = b'\x10'
ESCAPED_START = DLE + START
ESCAPED_END = DLE + END
ESCAPED_DLE = DLE + DLE

def _is_encrypted(message):
    return message[ENCRYPTION_FLAG_INDEX] == ENCRYPTION_FLAG_VALUE


class RiscoCrypt:
  def __init__(self, encoding='utf-8'):
    self._pseudo_buffer = None
    self._crc_decoded = list(map(int, base64.b64decode(CRC_ARRAY_BASE64).decode("utf-8")[1:-1].split(',')))
    self.encrypted_panel = False
    self._encoding = encoding

  def set_panel_id(self, panel_id):
    self._pseudo_buffer = RiscoCrypt._create_pseudo_buffer(panel_id)

  def encode(self, cmd_id, command, force_crypt=False):
    encrypted = bytearray()
    encrypted.append(2)
    encrypt = force_crypt or self.encrypted_panel
    if encrypt:
      encrypted.append(17)

    full_cmd = f'{cmd_id:02d}{command}\x17'
    crc = self._get_crc(full_cmd)
    full_cmd += crc
    chars = self._encrypt_chars(full_cmd.encode(self._encoding), encrypt)
    encrypted += chars

    encrypted.append(3)
    return encrypted;

  def decode(self, chars):
    self.encrypted_panel = _is_encrypted(chars)
    decrypted_chars = self._decrypt_chars(chars)
    decrypted = decrypted_chars.decode(self._encoding)
    raw_command = decrypted[0:decrypted.index('\x17')+1]
    command, crc = decrypted.split('\x17')

    if command[0] in ['N','B']:
      cmd_id = None
      command_string = command
    else:
      cmd_id = int(command[:2])
      command_string = command[2:]

    return [cmd_id, command_string, self._valid_crc(raw_command, crc)]

  def _encrypt_chars(self, chars, encrypt):
    position = 0;
    if encrypt:
      chars = bytearray(map(self._encrypt_decrypt_char, chars, range(len(chars))))
    chars = chars.replace(DLE, ESCAPED_DLE)
    chars = chars.replace(START, ESCAPED_START)
    chars = chars.replace(END, ESCAPED_END)

    return chars

  def _decrypt_chars(self, chars):
    decrypt = _is_encrypted(chars)
    initial_index = 2 if decrypt else 1
    escaped = chars[initial_index:-1]
    escaped = escaped.replace(ESCAPED_DLE, DLE)
    escaped = escaped.replace(ESCAPED_START, START)
    escaped = escaped.replace(ESCAPED_END, END)

    if decrypt:
      return bytes(map(self._encrypt_decrypt_char, escaped, range(len(escaped))))
    else:
      return escaped

  def _encrypt_decrypt_char(self, char, position):
    return char ^ self._pseudo_buffer[position]

  def _create_pseudo_buffer(panel_id):
    buffer_length = 255
    pseudo_buffer = bytearray(buffer_length)
    if panel_id == 0:
      return pseudo_buffer
    pid = panel_id
    num_array = [2, 4, 16, 32768]
    for i in range(buffer_length):
      n1 = 0
      n2 = 0
      for n1 in range(4):
        if (pid & num_array[n1]) > 0:
          n2 ^= 1
      pid = pid << 1 | n2
      pseudo_buffer[i] = (pid & buffer_length)
    return pseudo_buffer

  def _valid_crc(self, command, crc):
    if len(crc) != 4:
      return False

    for char in crc:
      if ord(char) > 127:
        return False

    computed = self._get_crc(command)
    return computed == crc

  def _get_crc(self, command):
    crc_base = 65535
    byte_buffer = bytearray(command, self._encoding)

    for b in byte_buffer:
      crc_base = crc_base >> 8 ^ self._crc_decoded[crc_base & 255 ^ b]

    return f'{crc_base:04X}'

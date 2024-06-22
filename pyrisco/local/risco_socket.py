import asyncio
from .risco_crypt import RiscoCrypt, ESCAPED_END, END
from pyrisco.common import UnauthorizedError, CannotConnectError, OperationError

MIN_CMD_ID = 1
MAX_CMD_ID = 49

class RiscoSocket:
  def __init__(self, host, port, code, **kwargs):
    self._host = host
    self._port = port
    self._code = code
    self._encoding = kwargs.get('encoding', 'utf-8')
    self._max_concurrency = kwargs.get('concurrency', 4)
    self._communication_delay = kwargs.get('communication_delay', 1)
    self._reader = None
    self._writer = None
    self._crypt = None
    self._listen_task = None
    self._keep_alive_task = None
    self._semaphore = None
    self._queue = None

  @property
  def queue(self):
    return self._queue

  async def connect(self):
    self._cmd_id = 0
    try:
      self._semaphore = asyncio.Semaphore(self._max_concurrency)
      self._futures = [None for i in range(MIN_CMD_ID, MIN_CMD_ID + MAX_CMD_ID)]
      self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
      if self._communication_delay > 0:
        await asyncio.sleep(self._communication_delay)
      self._queue = asyncio.Queue()
      self._listen_task = asyncio.create_task(self._listen())
      self._crypt = RiscoCrypt(self._encoding)
      panel_id = int(await self.send_result_command('RID'), 16)
      self._crypt.set_panel_id(panel_id)
      if not await self.send_ack_command('LCL'):
        raise CannotConnectError
      command = f'RMT={self._code}'
      if not await self.send_ack_command(command):
        raise UnauthorizedError

      self._keep_alive_task = asyncio.create_task(self._keep_alive())
    except Exception as exc:
      await self._close()
      raise CannotConnectError from exc

  async def disconnect(self):
    if self._writer:
      try:
        await self.send_ack_command('DCN')
      except OperationError:
        # safe to ignore these when disconnecting
        pass
      finally:
        await self._close()

  async def _listen(self):
    while True:
      try:
        cmd_id, command, crc = await self._read_command()
        if not cmd_id:
          self._decrement_cmd_id()
          raise OperationError(f'Risco error: {command}')
        if cmd_id <= MAX_CMD_ID:
          future = self._futures[cmd_id-1]
          self._futures[cmd_id-1] = None
          if not crc:
            future.set_exception(OperationError(f'cmd_id: {cmd_id}, Wrong CRC'))
          elif command[0] in ['N', 'B']:
            future.set_exception(OperationError(f'cmd_id: {cmd_id}, Risco error: {command}'))
          else:
            future.set_result(command)
        else:
          await self._handle_incoming(cmd_id, command, crc)
      except ConnectionResetError as error:
        await self._queue.put(error)
        break
      except Exception as error:
        await self._queue.put(error)


  async def _keep_alive(self):
    while True:
      try:
        await self.send_result_command("CLOCK")
      except OperationError as error:
        await self._queue.put(error)

      await asyncio.sleep(5)

  async def send_ack_command(self, command):
    command = await self.send_command(command)
    return command == 'ACK'

  async def send_result_command(self, command):
    command = await self.send_command(command)
    return command.split("=")[1]

  async def send_command(self, command, force_encryption=False):
    async with self._semaphore:
      self._increment_cmd_id()
      self._write_command(self._cmd_id, command, force_encryption)
      future = asyncio.Future()
      self._futures[self._cmd_id-1] = future
      try:
        return await asyncio.wait_for(future, 10)
      except asyncio.TimeoutError:
        raise OperationError(f'Timeout in command: {command}')

  async def _handle_incoming(self, cmd_id, command, crc):
    self._write_command(cmd_id, 'ACK')
    if not crc:
      await self._queue.put(OperationError(f'cmd_id: {cmd_id}, Wrong CRC'))
    else:
      await self._queue.put(command)

  async def _read_command(self):
    buffer = await self._reader.readuntil(END)
    while buffer.endswith(ESCAPED_END):
      buffer += await self._reader.readuntil(END)
    return self._crypt.decode(buffer)

  def _write_command(self, cmd_id, command, force_encryption=False):
    buffer = self._crypt.encode(cmd_id, command, force_encryption)
    self._writer.write(buffer)

  async def _close(self):
    if self._keep_alive_task:
      self._keep_alive_task.cancel()
      self._keep_alive_task = None

    if self._listen_task:
      self._listen_task.cancel()
      self._listen_task = None

    if self._writer:
      self._writer.close()
      await self._writer.wait_closed()
    self._crypt = None
    self._writer = None
    self._reader = None
    self._semaphore = None
    self._queue = None
    # Risco needs a few seconds to reset its encryption state before accepting a new connection
    # If we don't sleep here, the next connection will be encrypted before we get the panel id.
    await asyncio.sleep(5)

  def _increment_cmd_id(self):
    self._cmd_id += 1
    if self._cmd_id > MAX_CMD_ID:
      self._cmd_id = MIN_CMD_ID

  def _decrement_cmd_id(self):
    self._cmd_id -= 1
    if self._cmd_id < MIN_CMD_ID:
      self._cmd_id = MAX_CMD_ID

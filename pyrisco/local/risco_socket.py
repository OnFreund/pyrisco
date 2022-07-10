import asyncio
from .risco_crypt import RiscoCrypt
from pyrisco.risco import UnauthorizedError, CannotConnectError, OperationError

class RiscoSocket:
  def __init__(self, options):
    self._timeout = 3
    self._panel_id = options['panel_id']
    self._encoding = options['encoding']
    self._host = options['host']
    self._port = options['port']
    self._code_length = options['code_length']
    self._code = options['code']
    self._reader = None
    self._writer = None
    

  async def connect(self):
    self._cmd_id = 0
    self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
    self._crypt = RiscoCrypt(self._panel_id, self._encoding)
    try:
      command = f'RMT={self._code:0{self._code_length}d}'
      if not await self.send_ack_command(command):
        raise CannotConnectError

      if not await self.send_ack_command('LCL'):
        raise CannotConnectError

      self._crypt.encrypted_panel = True
      if not await self.send_ack_command('LCL'):
        raise UnauthorizedError
    except:
      await self._close()
      raise

  async def disconnect(self):
    if self._writer:
      try:
        await self.send_command('DCN', False, False)
      finally:
        await self._close()

  async def send_ack_command(self, command, prog_cmd=False):
    command = await self.send_command(command, prog_cmd)
    return command == 'ACK'

  async def send_command(self, command, prog_cmd=False, force_encryption=False):
    # while (this.inProg && !progCmd) {
    #   // if we are in programming mode, wait 5s before retry
    #   logger.log('debug', `sendCommand: Waiting for programming mode to exit`);
    #   await new Promise(r => setTimeout(r, 5000));
    # }
    # if (this.inProg && !progCmd) {
    #   const message = `sendCommand: Programming mode did not exit after delay, rejecting command`;
    #   logger.log('error', message);
    #   throw new Error(message);
    # }

    # if (!cmdCtx) {
    #   cmdCtx = this.allocateCmdCtx(commandStr);
    # }
    
    # let responseTimeoutDelay: number;
    # if (progCmd) {
    #   responseTimeoutDelay = 29000;
    # } else {
    #   responseTimeoutDelay = 5000;
    # }

    self._advance_cmd_id()
    buffer = self._crypt.encode(self._cmd_id, command, force_encryption);
    self._writer.write(buffer);
    cmd_id = 0
    while cmd_id != self._cmd_id:
      response = await asyncio.wait_for(self._reader.readuntil(b'\x03'), self._timeout)
      while response.endswith(b'\x10\x03'):
        response += await asyncio.wait_for(self._reader.readuntil(b'\x03'), self._timeout)
      cmd_id, command, crc = self._crypt.decode(response)
      if cmd_id != self._cmd_id:
        print(self._crypt.decode(response))

    if not crc:
      raise OperationError

    return command

  async def _close(self):
    self._writer.close()
    await self._writer.wait_closed()
    self._writer = None
    self._reader = None

  def _advance_cmd_id(self):
    self._cmd_id += 1
    if self._cmd_id > 49:
      self._cmd_id = 1

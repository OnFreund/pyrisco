"""check_diagnostics must catch each diagnostic it exists for.

Each sample runs in its own interpreter under -X dev, so what it prints never
reaches this suite's own output (which the checker reads in CI).
"""

import os
import subprocess
import sys
import unittest

from check_diagnostics import findings

SAMPLES = {
    'coroutine never awaited': '''
async def work():
  pass
work()
''',
    'task exception never retrieved': '''
import asyncio
async def boom():
  raise RuntimeError('boom')
async def main():
  asyncio.create_task(boom())
  await asyncio.sleep(0.05)
asyncio.run(main())
''',
    'pending task destroyed': '''
import asyncio
async def wait_forever():
  await asyncio.Event().wait()
loop = asyncio.new_event_loop()
loop.create_task(wait_forever())
loop.run_until_complete(asyncio.sleep(0))
loop.close()
''',
    'unclosed socket': '''
import socket
s = socket.socket()
del s
''',
    'callback that raises': '''
import asyncio
def broken():
  raise RuntimeError('in a callback')
async def main():
  asyncio.get_running_loop().call_soon(broken)
  await asyncio.sleep(0.05)
asyncio.run(main())
''',
    'connection handler that raises': '''
import asyncio
async def handler(reader, writer):
  writer.close()
  raise RuntimeError('in a connection handler')
async def main():
  server = await asyncio.start_server(handler, '127.0.0.1', 0)
  port = server.sockets[0].getsockname()[1]
  reader, writer = await asyncio.open_connection('127.0.0.1', port)
  await reader.read()
  writer.close()
  await writer.wait_closed()
  server.close()
  await asyncio.sleep(0.1)
asyncio.run(main())
''',
    'exception in __del__': '''
class Leaky:
  def __del__(self):
    raise RuntimeError('in __del__')
Leaky()
''',
}


def _run(code):
  result = subprocess.run([sys.executable, '-X', 'dev', '-c', code],
                          capture_output=True, text=True, timeout=60)
  return result.stdout + result.stderr


class CheckDiagnosticsTest(unittest.TestCase):

  def test_each_kind_of_diagnostic_is_caught(self):
    for name, code in SAMPLES.items():
      with self.subTest(name):
        output = _run(code)
        self.assertTrue(findings(output), f'not caught; the sample printed:\n{output}')

  def test_the_word_alone_is_not_a_diagnostic(self):
    """Allow ordinary uses of diagnostic-related words without failing the test run."""
    self.assertEqual(findings('test_a_socket_left_unclosed_is_closed (x.Y) ... ok'), [])
    self.assertEqual(len(findings('ResourceWarning: unclosed <socket.socket fd=3>\n'
                                  'Warning: unclosed transport <_SelectorSocketTransport>')), 2)

  def test_a_clean_run_passes(self):
    self.assertEqual(findings(_run('print("ok")')), [])

  def test_no_test_in_the_suite_prints_as_a_diagnostic(self):
    """Keep verbose test names and docstrings from triggering the diagnostics checker.

    Unittest prints each test name and the first docstring line under -v, so
    those lines must not resemble an actual runtime diagnostic.
    """
    printed = []
    suites = [unittest.defaultTestLoader.discover(os.path.dirname(os.path.abspath(__file__)))]
    while suites:
      for test in suites.pop():
        if isinstance(test, unittest.TestSuite):
          suites.append(test)
        else:
          printed.append(f'{test} ... ok')
          printed.append(f'{test.shortDescription()} ... ok')
    self.assertGreater(len(printed), 100)
    self.assertEqual(findings('\n'.join(printed)), [])

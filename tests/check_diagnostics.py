"""Fail a test run whose output shows asyncio or resource diagnostics.

    python -X dev -m unittest discover -s tests -v 2>&1 | tee test-output.log
    python tests/check_diagnostics.py test-output.log

Under -X dev these are printed rather than raised, so a test that leaks a
socket or leaves a task's exception unretrieved still passes. The listener
and keep-alive bugs this suite guards against show up exactly this way.
"""

import re
import sys

PATTERNS = (
    'was never awaited',
    'exception was never retrieved',
    'Task was destroyed but it is pending',
    'ResourceWarning',
    'Exception ignored',
    # asyncio's and the socket module's own wording, not just the word.
    'unclosed <',
    'unclosed transport',
    'unclosed event loop',
    'Exception in callback',
    'Unhandled exception in client_connected_cb',
)
_MATCH = re.compile('|'.join(re.escape(p) for p in PATTERNS))


def findings(text):
  return [line for line in text.splitlines() if _MATCH.search(line)]


def main(path):
  with open(path, encoding='utf-8', errors='replace') as log:
    found = findings(log.read())
  for line in found:
    print(line)
  if found:
    print(f'{len(found)} diagnostic line(s) in {path}')
  return 1 if found else 0


if __name__ == '__main__':
  sys.exit(main(sys.argv[1]))

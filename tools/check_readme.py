#!/usr/bin/env python
"""Check that README.md was updated when code files changed in the commit.

This script should be run during pre-commit. It inspects the staged files; if any code files
(.py, .ipynb) are staged and README.md is not staged or unchanged, it fails.
"""
import subprocess
import sys

# file extensions considered "code"
CODE_EXTS = {'.py', '.ipynb'}

try:
    # Get staged files
    res = subprocess.run(['git', 'diff', '--cached', '--name-only'], stdout=subprocess.PIPE, check=True)
    files = res.stdout.decode().splitlines()
except subprocess.CalledProcessError as e:
    print('Failed to get staged files:', e)
    sys.exit(1)

code_changed = [f for f in files if any(f.endswith(ext) for ext in CODE_EXTS)]
readme_changed = any(f.lower() == 'readme.md' for f in files)

if code_changed and not readme_changed:
    print('\nERROR: You are changing code files but did not update README.md.')
    print('Changed code files:')
    for f in code_changed:
        print('  -', f)
    print('\nPlease update README.md describing your change and stage it before committing.')
    sys.exit(1)

# all good
sys.exit(0)

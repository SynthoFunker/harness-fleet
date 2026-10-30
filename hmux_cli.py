#!/usr/bin/env python3
"""
HMUX CLI Entrypoint
"""

import os
import sys

# Add package directory to path
package_dir = os.path.dirname(os.path.abspath(__file__))
if package_dir not in sys.path:
    sys.path.insert(0, package_dir)

from hmux.cli import cli

if __name__ == "__main__":
    cli()

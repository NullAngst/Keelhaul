import sys

from .main import main

# Guarded so importing this module (an import sweep, a test) never starts the GUI.
if __name__ == "__main__":
    sys.exit(main())

"""Compatible command-line launcher. Uses the same safe startup as the shortcut."""
from scripts.launch_app import main

if __name__ == "__main__":
    raise SystemExit(main())

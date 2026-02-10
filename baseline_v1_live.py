"""
Launcher script — allows running from the project root:
    python baseline_v1_live.py --auto
Equivalent to:
    python -m baseline_v1_live.baseline_v1_live --auto
"""
from baseline_v1_live.baseline_v1_live import main

if __name__ == "__main__":
    main()

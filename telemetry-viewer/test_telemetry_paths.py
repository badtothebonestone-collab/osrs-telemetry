from pathlib import Path
import runpy


TEST_PATH = Path(__file__).resolve().parent / "tests" / "test_telemetry_paths.py"

if __name__ == "__main__":
    runpy.run_path(str(TEST_PATH), run_name="__main__")

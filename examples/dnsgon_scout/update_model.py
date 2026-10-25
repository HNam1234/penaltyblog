"""Refresh public results and atomically export a model. No Telegram credentials."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from model_data import refresh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = refresh(args.data)
    print(json.dumps({'results_refresh': report}), flush=True)
    subprocess.run([sys.executable, str(Path(__file__).with_name('train.py')), '--live-only',
                    '--data', args.data, '--output', args.output], check=True)


if __name__ == '__main__':
    main()

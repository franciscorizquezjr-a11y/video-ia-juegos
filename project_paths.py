"""Canonical export layout shared by all render entry points."""
from pathlib import Path
from urllib.parse import quote
ROOT=Path(__file__).resolve().parent
OUTPUT_ROOT=ROOT/'output'
APP_OUTPUT_DIR=OUTPUT_ROOT/'videos'/'Por_clasificar'
WORK_OUTPUT_DIR=OUTPUT_ROOT/'trabajo'
MANAGEMENT_DIR=OUTPUT_ROOT/'gestion'
def output_url(path, root=None):
    relative=Path(path).resolve().relative_to(Path(root or OUTPUT_ROOT).resolve())
    return '/media/output/'+quote(relative.as_posix(),safe='/')
def app_output_url(filename):
    return output_url(APP_OUTPUT_DIR/filename)

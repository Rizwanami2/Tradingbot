"""Load a script from a skill folder in trader_skills."""

import importlib.util
import os

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(TEST_DIR))
SKILLS_DIR = os.path.join(PROJECT_ROOT, "trader_skills")
ROOT = SKILLS_DIR
FIX = os.path.join(PROJECT_ROOT, "tests", "fixtures")


def load(folder, name):
    path = os.path.join(SKILLS_DIR, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("trader_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

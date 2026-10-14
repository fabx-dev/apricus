#!/bin/bash

# Regenerate pypi-deps.json from pyproject.toml (the single dependency source).
set -e
cd "$(dirname "$0")"

flatpak-pip-generator --pyproject-file=../../pyproject.toml --output pypi-deps

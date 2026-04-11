#!/usr/bin/env bash
set -e

PROJECT_DIR="/root/saulinfo-site"

cd "${PROJECT_DIR}"
git pull origin main
docker compose up -d --build
docker image prune -f

echo "SaulInfo site updated"

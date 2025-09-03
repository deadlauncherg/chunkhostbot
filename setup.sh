#!/bin/bash
set -e

echo "🚀 Installing ChunkHost VPS Bot..."

# Update system
sudo apt-get update
sudo apt-get install -y python3 python3-pip docker.io git

# Install Python deps
pip3 install -r requirements.txt

# Create bot user
sudo useradd -r -s /bin/false chunkhostbot || true
sudo usermod -aG docker chunkhostbot

# Copy service file
sudo mkdir -p /etc/systemd/system/
sudo cp systemd/chunkhostbot.service /etc/systemd/system/chunkhostbot.service
sudo systemctl daemon-reload
sudo systemctl enable chunkhostbot.service
sudo systemctl start chunkhostbot.service

echo "✅ Installation complete!"
echo "Check logs with: sudo journalctl -u chunkhostbot.service -f"

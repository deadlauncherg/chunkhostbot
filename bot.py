import discord
from discord.ext import commands
from discord import ui, app_commands
import os
import random
import string
import json
import subprocess
from dotenv import load_dotenv
import asyncio
import datetime
import docker
import time
import logging
import traceback
import aiohttp
import socket
import re
import psutil
import platform
import shutil
from typing import Optional, Literal
import sqlite3
import pickle
import base64
import threading
from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit
import docker
import paramiko

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ChunkHost_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('ChunkHostBot')

# Load environment variables
load_dotenv()

# Bot configuration
TOKEN = os.getenv('DISCORD_TOKEN')
ADMIN_IDS = {int(id_) for id_ in os.getenv('ADMIN_IDS', '').split(',') if id_.strip()}
ADMIN_ROLE_ID = int(os.getenv('ADMIN_ROLE_ID', '0'))
WATERMARK = "ChunkHost VPS Service"
WELCOME_MESSAGE = "Welcome to ChunkHost! 🚀"
MAX_VPS_PER_USER = int(os.getenv('MAX_VPS_PER_USER', '3'))
DEFAULT_OS_IMAGE = os.getenv('DEFAULT_OS_IMAGE', 'ubuntu:22.04')
DOCKER_NETWORK = os.getenv('DOCKER_NETWORK', 'bridge')
MAX_CONTAINERS = int(os.getenv('MAX_CONTAINERS', '100'))
DB_FILE = 'chunkhost.db'
BACKUP_FILE = 'chunkhost_backup.pkl'

# Known miner process names/patterns
MINER_PATTERNS = [
    'xmrig', 'ethminer', 'cgminer', 'sgminer', 'bfgminer',
    'minerd', 'cpuminer', 'cryptonight', 'stratum', 'pool'
]

# ==============================
# Dockerfile Template (Patched)
# ==============================
DOCKERFILE_TEMPLATE = """
FROM {base_image}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \\
    apt-get install -y systemd systemd-sysv dbus sudo \\
                       curl gnupg2 apt-transport-https ca-certificates \\
                       software-properties-common \\
                       docker.io openssh-server tmate && \\
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN echo "root:{root_password}" | chpasswd

RUN useradd -m -s /bin/bash {username} && \\
    echo "{username}:{user_password}" | chpasswd && \\
    usermod -aG sudo {username}

RUN mkdir /var/run/sshd && \\
    sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config && \\
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config

RUN systemctl enable ssh && \\
    systemctl enable docker

RUN echo '{welcome_message}' > /etc/motd && \\
    echo 'echo "{welcome_message}"' >> /home/{username}/.bashrc && \\
    echo '{watermark}' > /etc/machine-info && \\
    echo 'chunkhostbot-{vps_id}' > /etc/hostname

RUN apt-get update && \\
    apt-get install -y neofetch htop nano vim wget git tmux net-tools dnsutils iputils-ping && \\
    apt-get clean && \\
    rm -rf /var/lib/apt/lists/*

# 🔥 Fake hardware specs for neofetch
RUN mkdir -p /etc/neofetch && \\
    echo 'print_info() { \\
    info "OS" "ChunkHost VPS" \\
    info "Host" "ChunkHost Virtual Server" \\
    info "Kernel" "Linux 6.1" \\
    info "Uptime" "$(uptime -p)" \\
    info "CPU" "AMD Ryzen 9" \\
    info "Memory" "32GB" \\
    info "Disk" "64GB" \\
}' > /etc/neofetch/config.conf

STOPSIGNAL SIGRTMIN+3

CMD ["/sbin/init"]
"""

# ==============================
# Database & Bot Logic
# ==============================

class Database:
    def __init__(self, db_file=DB_FILE):
        self.conn = sqlite3.connect(db_file)
        self.create_tables()

    def create_tables(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS vps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    vps_id TEXT NOT NULL,
                    container_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def add_vps(self, user_id, vps_id, container_id, username, password):
        with self.conn:
            self.conn.execute("""
                INSERT INTO vps (user_id, vps_id, container_id, username, password)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, vps_id, container_id, username, password))

    def get_vps_by_user(self, user_id):
        with self.conn:
            return self.conn.execute("SELECT * FROM vps WHERE user_id=?", (user_id,)).fetchall()

    def delete_vps(self, vps_id):
        with self.conn:
            self.conn.execute("DELETE FROM vps WHERE vps_id=?", (vps_id,))

# ==============================
# Discord Bot
# ==============================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

db = Database()
docker_client = docker.from_env()

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ChunkHostBot)")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")

# ==============================
# Commands
# ==============================

@bot.tree.command(name="create_vps", description="Create your own ChunkHost VPS")
async def create_vps(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    user_id = interaction.user.id
    existing_vps = db.get_vps_by_user(user_id)
    if len(existing_vps) >= MAX_VPS_PER_USER:
        await interaction.followup.send("⚠️ You already have the maximum number of VPS allowed.")
        return

    vps_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    username = f"user{random.randint(1000,9999)}"
    user_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
    root_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))

    dockerfile = DOCKERFILE_TEMPLATE.format(
        base_image=DEFAULT_OS_IMAGE,
        root_password=root_password,
        username=username,
        user_password=user_password,
        welcome_message=WELCOME_MESSAGE,
        watermark=WATERMARK,
        vps_id=vps_id
    )

    dockerfile_path = f"./Dockerfile_{vps_id}"
    with open(dockerfile_path, "w") as f:
        f.write(dockerfile)

    image_tag = f"chunkhost_image_{vps_id}"
    container_name = f"chunkhost_{vps_id}"

    try:
        image, logs = docker_client.images.build(path=".", dockerfile=dockerfile_path, tag=image_tag)
        container = docker_client.containers.run(
            image=image_tag,
            name=container_name,
            detach=True,
            tty=True,
            stdin_open=True,
            network=DOCKER_NETWORK,
            privileged=True
        )

        db.add_vps(user_id, vps_id, container.id, username, user_password)

        await interaction.followup.send(
            f"✅ Your VPS is ready!\n"
            f"🆔 ID: `{vps_id}`\n"
            f"👤 Username: `{username}`\n"
            f"🔑 Password: `{user_password}`\n"
            f"📦 Root Password: `{root_password}`"
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to create VPS: {e}")
    finally:
        os.remove(dockerfile_path)

@bot.tree.command(name="list_vps", description="List your VPS instances")
async def list_vps(interaction: discord.Interaction):
    vps_list = db.get_vps_by_user(interaction.user.id)
    if not vps_list:
        await interaction.response.send_message("❌ You don’t have any VPS yet.", ephemeral=True)
        return

    msg = "**📦 Your VPS Instances:**\n"
    for v in vps_list:
        msg += f"🆔 ID: `{v[2]}` | 👤 User: `{v[4]}` | Created: {v[6]}\n"

    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="delete_vps", description="Delete a VPS by ID")
async def delete_vps(interaction: discord.Interaction, vps_id: str):
    vps_list = db.get_vps_by_user(interaction.user.id)
    target_vps = [v for v in vps_list if v[2] == vps_id]

    if not target_vps:
        await interaction.response.send_message("❌ VPS not found or not owned by you.", ephemeral=True)
        return

    container_id = target_vps[0][3]
    try:
        container = docker_client.containers.get(container_id)
        container.stop()
        container.remove()
        db.delete_vps(vps_id)
        await interaction.response.send_message(f"✅ VPS `{vps_id}` deleted successfully.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to delete VPS: {e}", ephemeral=True)

# ==============================
# Run Bot
# ==============================
if __name__ == "__main__":
    bot.run(TOKEN)

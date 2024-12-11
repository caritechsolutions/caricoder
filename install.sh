#!/bin/bash
set -e

# Log setup
exec 1> >(tee "install.log") 2>&1
echo "Starting CariCoder installation at $(date)"

# Check root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

# Install initial requirements
apt update
apt install -y git curl

# Clone repository
cd /root
rm -rf caricoder
git clone https://github.com/caritechsolutions/caricoder.git
cd caricoder

# Install system packages
xargs apt install -y < system-packages.txt

# Install Python packages
#pip install -r requirements.txt

sudo apt install $(cat requirements.txt | xargs)

# Setup web files
rm -rf /var/www/html/*
cp -r web/html/* /var/www/html/
chown -R www-data:www-data /var/www/html

# Install and enable services
cp services/* /etc/systemd/system/
systemctl daemon-reload

# Enable specific services
declare -a services=(
    "caricoder_sch"
    "channel-manager"
    "channel-monitor"
    "metrics-collector"
    "stats_api"
)

for service in "${services[@]}"; do
    echo "Enabling and starting $service"
    systemctl enable "$service.service"
    systemctl restart "$service.service"
    systemctl status "$service.service"
done

echo "Installation complete! Check install.log for details."
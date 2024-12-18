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
# Check Ubuntu version
ubuntu_version=$(lsb_release -rs)
version_ge_24_04=$(echo "$ubuntu_version >= 24.04" | bc -l)

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
# Install essential packages via apt
apt install -y python3-flask python3-flask-cors python3-redis python3-psutil python3-yaml python3-gi python3-pip

# Install Python packages based on Ubuntu version
#pip install --break-system-packages aiohttp psutil redis Flask Flask-Cors PyYAML
if [ "$version_ge_24_04" -eq 1 ]; then
    pip install --break-system-packages aiohttp psutil redis Flask Flask-Cors PyYAML
else
    pip install aiohttp psutil redis Flask Flask-Cors PyYAML
fi

# Configure nginx
echo "Configuring nginx..."
if [ -f /etc/nginx/sites-available/default ]; then
    mv /etc/nginx/sites-available/default /etc/nginx/sites-available/default.bak
fi
cp nginx/default /etc/nginx/sites-available/default
if [ ! -f /etc/nginx/sites-enabled/default ]; then
    ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
fi
# Test nginx configuration
nginx -t
# If test passes, restart nginx
if [ $? -eq 0 ]; then
    systemctl restart nginx
else
    echo "Nginx configuration test failed"
    exit 1
fi
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
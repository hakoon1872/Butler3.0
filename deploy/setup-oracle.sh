#!/usr/bin/env bash
# JARVIS — Oracle Always Free setup (run ON the VM, inside the project folder)
# Ubuntu 24.04 ARM · Caddy auto-HTTPS · systemd · token · nightly backup
set -euo pipefail
cd "$(dirname "$0")/.."   # project root (folder containing server.py)
echo "== 1/6 packages =="
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip curl sqlite3 debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -y && sudo apt-get install -y caddy

echo "== 2/6 python env =="
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "== 3/6 Kokoro voice files (skip if already present) =="
[ -f kokoro-v1.0.onnx ] || curl -L -o kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
[ -f voices-v1.0.bin ] || curl -L -o voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

echo "== 4/6 .env (key + access token) =="
touch .env
grep -q '^GEMINI_API_KEY=' .env || echo 'GEMINI_API_KEY=' >> .env
if ! grep -q '^JARVIS_ACCESS_TOKEN=..*' .env; then
  TOK=$(openssl rand -hex 24)
  sed -i '/^JARVIS_ACCESS_TOKEN=/d' .env
  echo "JARVIS_ACCESS_TOKEN=$TOK" >> .env
  echo ">>> YOUR ACCESS TOKEN (save it, the HUD lock screen asks for it): $TOK"
fi
echo ">>> Now edit .env and paste your Gemini key after GEMINI_API_KEY= :  nano .env"

echo "== 5/6 systemd service =="
PROJ=$(pwd)
sudo tee /etc/systemd/system/jarvis.service >/dev/null <<UNIT
[Unit]
Description=Jarvis Night Desk
After=network.target
[Service]
User=$USER
WorkingDirectory=$PROJ
ExecStart=$PROJ/venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now jarvis

echo "== 6/6 Caddy HTTPS + nightly backup =="
IP=$(curl -s https://ifconfig.me)
sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$IP.sslip.io {
    reverse_proxy 127.0.0.1:8000
}
CADDY
sudo systemctl restart caddy
mkdir -p backups
( crontab -l 2>/dev/null; echo "0 3 * * * cd $PROJ && sqlite3 butler.db \".backup backups/butler-\$(date +\%F).db\" && find backups -name 'butler-*.db' -mtime +14 -delete" ) | crontab -
echo ""
echo "DONE. Open https://$IP.sslip.io on your phone."
echo "Reminder: ports 80+443 must be open in BOTH the Oracle security list and iptables:"
echo "  sudo iptables -I INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT && sudo apt-get install -y iptables-persistent"

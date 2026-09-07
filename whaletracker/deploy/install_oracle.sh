#!/usr/bin/env bash
# Cai UNI Whale Tracker len Oracle Cloud (Ubuntu 22.04+ hoac Oracle Linux 9).
#
# QUAN TRONG: khi tao instance hay chon region Singapore / Tokyo / Frankfurt.
# Binance chan IP My (HTTP 451). Tool co du phong OKX/Kraken/CoinGecko nhung
# chon dung region tu dau van hon.
#
# Khuyen nghi shape: VM.Standard.A1.Flex (ARM, 4 OCPU / 24GB - always free).
#
# Dung:  sudo bash install_oracle.sh
set -euo pipefail

APP_USER="${SUDO_USER:-$USER}"
APP_DIR="/opt/whaletracker"
ENV_FILE="/etc/whaletracker.env"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Cai goi he thong"
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip sqlite3 ca-certificates
else
    dnf install -y python3 python3-pip sqlite ca-certificates
fi

echo "==> Chep ma nguon toi $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR/wt" "$SRC_DIR/config.json" "$SRC_DIR/requirements.txt" "$APP_DIR/"
mkdir -p "$APP_DIR/data" "$APP_DIR/out"
# CSV seed la tuy chon - neu co thi mang theo
[ -d "$SRC_DIR/../260731_data_transfer" ] && cp -r "$SRC_DIR/../260731_data_transfer" "$APP_DIR/" || true
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

echo "==> Tao virtualenv"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Tao file bi mat $ENV_FILE (chmod 600)"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'EOF'
# Bi mat cua UNI Whale Tracker. KHONG commit file nay.
# Email (Gmail: bat 2FA roi tao App Password 16 ky tu tai
#   https://myaccount.google.com/apppasswords)
WT_EMAIL_ENABLED=false
WT_EMAIL_USERNAME=
WT_EMAIL_APP_PASSWORD=
WT_EMAIL_TO=

# Telegram (chat @BotFather -> /newbot; lay chat_id qua /getUpdates)
WT_TELEGRAM_ENABLED=false
WT_TELEGRAM_TOKEN=
WT_TELEGRAM_CHAT_ID=

# Chi canh bao vi tu bao nhieu diem tro len
WT_MIN_SCORE=3.0
WT_POLL_SEC=45
EOF
    chmod 600 "$ENV_FILE"
    echo "    -> HAY SUA $ENV_FILE va dien thong tin truoc khi khoi dong monitor"
else
    echo "    -> da ton tai, giu nguyen"
fi

echo "==> Cai systemd unit"
sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@APP_USER@|$APP_USER|g" \
    "$SRC_DIR/deploy/wt-monitor.service" > /etc/systemd/system/wt-monitor.service
sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@APP_USER@|$APP_USER|g" \
    "$SRC_DIR/deploy/wt-analyze.service" > /etc/systemd/system/wt-analyze.service
cp "$SRC_DIR/deploy/wt-analyze.timer" /etc/systemd/system/wt-analyze.timer
systemctl daemon-reload

cat <<EOF

========================================================================
Cai xong. Cac buoc tiep theo:

1. Dien thong tin canh bao:
     sudo nano $ENV_FILE

2. Chay phan tich lan dau (mat ~40-60 phut, resume duoc neu dut):
     cd $APP_DIR && ./venv/bin/python -m wt all

3. Thu kenh canh bao:
     cd $APP_DIR && set -a && . $ENV_FILE && set +a && ./venv/bin/python -m wt monitor --test-alert

4. Bat chay nen:
     sudo systemctl enable --now wt-monitor.service   # theo doi lien tuc
     sudo systemctl enable --now wt-analyze.timer     # cham diem lai moi 6h

5. Xem log:
     journalctl -u wt-monitor -f

Mo tuong lua neu muon xem bao cao HTML tu xa (khong bat buoc):
     $APP_DIR/out/report.html  -- hoac tai ve bang scp
========================================================================
EOF

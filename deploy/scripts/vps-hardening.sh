#!/usr/bin/env bash
# =====================================================================
# vps-hardening.sh — UFW + Fail2ban para OSINT Nexus
# Deriva de SPEC-04 §3. Idempotente: puede ejecutarse varias veces.
# Ejecutar como root en el VPS.
# =====================================================================
set -euo pipefail

SSH_PORT="${SSH_PORT:-22}"

echo "[1/5] Actualizando índice de paquetes..."
apt-get update -y

echo "[2/5] Instalando UFW y Fail2ban si faltan..."
apt-get install -y ufw fail2ban

echo "[3/5] Configurando UFW (solo SSH, 80 y 443)..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}/tcp" comment 'SSH'
ufw allow 80/tcp  comment 'HTTP (redirect)'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable
ufw status verbose

echo "[4/5] Endureciendo SSH..."
SSHD_CONF=/etc/ssh/sshd_config.d/99-osint-nexus.conf
cat > "${SSHD_CONF}" <<'EOF'
PermitRootLogin prohibit-password
PasswordAuthentication no
ChallengeResponseAuthentication no
X11Forwarding no
MaxAuthTries 3
EOF
systemctl reload sshd || systemctl reload ssh || true

echo "[5/5] Configurando Fail2ban para SSH..."
cat > /etc/fail2ban/jail.d/osint-nexus.local <<EOF
[sshd]
enabled = true
port    = ${SSH_PORT}
maxretry = 3
bantime  = 3600
findtime = 600
EOF
systemctl enable fail2ban
systemctl restart fail2ban

echo "Hardening completado. Puertos abiertos: ${SSH_PORT}, 80, 443."

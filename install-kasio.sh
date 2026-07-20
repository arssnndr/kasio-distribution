#!/bin/bash
# ============================================================================
# KASIO One-Shot Installer for Hermes Agent
# ============================================================================
# Usage:
#   ./install-kasio.sh
#   curl -fsSL https://raw.githubusercontent.com/arssnndr/kasio-distribution/main/install-kasio.sh | bash
#
# What it does:
#   1. Verify Hermes installed
#   2. Install kasio distribution via hermes profile install
#   3. Prompt for required env values
#   4. Activate profile
#   5. Verify installation
# ============================================================================

set -e

REPO="${KASIO_REPO:-github.com/arssnndr/kasio-distribution}"
PROFILE_NAME="kasio"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}╔════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  KASIO — One-Shot Installer            ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════╝${NC}"
echo

# 1. Verify Hermes
echo -e "${BLUE}[1/6]${NC} Checking Hermes installation..."
if ! command -v hermes &> /dev/null; then
    echo -e "${RED}✗ Hermes not found.${NC}"
    echo -e "  Install dulu:"
    echo -e "    ${YELLOW}curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Hermes found${NC}"

# 2. Install / update distribution
echo -e "${BLUE}[2/6]${NC} Installing kasio distribution from ${REPO}..."

# Resolve Hermes home (needed untuk detect existing profile)
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

# Detect: apakah profile sudah ada?
PROFILE_DIR="${HERMES_HOME}/profiles/${PROFILE_NAME}"
if [ -d "${PROFILE_DIR}" ]; then
    echo -e "${YELLOW}! Profile '${PROFILE_NAME}' sudah ada — pakai 'hermes profile update' (preserve user data)${NC}"
    hermes profile update "${PROFILE_NAME}" --yes
else
    hermes profile install "${REPO}" --name "${PROFILE_NAME}" --yes
fi
echo -e "${GREEN}✓ Installed${NC}"

# 3. Env file path
ENV_FILE="${PROFILE_DIR}/.env"
echo -e "${GREEN}✓ Env file: ${ENV_FILE}${NC}"

# Ensure directory + file exists (hermes profile install doesn't auto-create .env)
mkdir -p "$(dirname "${ENV_FILE}")"
touch "${ENV_FILE}"

# 4. Prompt for env values
echo -e "${BLUE}[3/6]${NC} Setup environment variables..."
echo

append_if_empty() {
    local key="$1"
    local desc="$2"
    local value
    if grep -q "^${key}=.\+" "${ENV_FILE}" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} ${key} (already set, skipping)"
        return
    fi
    echo -e "${YELLOW}→ ${key}${NC}"
    echo "  ${desc}"
    read -p "  Value: " value
    echo "${key}=${value}" >> "${ENV_FILE}"
    echo
}

append_if_empty "NOTION_API_KEY" "Notion integration key (https://www.notion.so/my-integrations)"
append_if_empty "KASIO_TRANSACTIONS_DS_ID" "Notion data source ID untuk transactions DB"
append_if_empty "KASIO_ACCOUNTS_DS_ID" "Notion data source ID untuk accounts DB"

# Optional
if ! grep -q "^MINIMAX_API_KEY=.\+" "${ENV_FILE}" 2>/dev/null; then
    echo -e "${YELLOW}→ MINIMAX_API_KEY${NC} (optional — untuk vision reading foto struk)"
    echo "  Skip kalau tidak pakai vision (tekan Enter)"
    read -p "  Value (optional): " mm_key
    if [ -n "${mm_key}" ]; then
        echo "MINIMAX_API_KEY=${mm_key}" >> "${ENV_FILE}"
    fi
fi

# 5. Activate profile
echo -e "${BLUE}[4/6]${NC} Activating kasio profile..."
hermes profile use "${PROFILE_NAME}" --yes

# 6. Verify
echo -e "${BLUE}[5/6]${NC} Verifying installation..."
if hermes plugins list 2>&1 | grep -q "kasio-notion"; then
    echo -e "  ${GREEN}✓${NC} Plugin kasio-notion loaded"
else
    echo -e "  ${RED}✗${NC} Plugin not found"
fi

if hermes tools list 2>&1 | grep -q "kasio"; then
    echo -e "  ${GREEN}✓${NC} Tools registered"
else
    echo -e "  ${RED}✗${NC} Tools missing"
fi

if hermes skills list 2>&1 | grep -q "kasio"; then
    echo -e "  ${GREEN}✓${NC} Skill loaded"
else
    echo -e "  ${RED}✗${NC} Skill not found"
fi

echo
echo -e "${BLUE}[6/6]${NC} ${GREEN}Done!${NC}"
echo
echo -e "${CYAN}Cara pakai:${NC}"
echo -e "  Chat di Telegram/CLI/Desktop, bilang:"
echo -e "    ${YELLOW}\"catat makan siang 35rb\"${NC}    → wizard catat transaksi"
echo -e "    ${YELLOW}\"/saldo\"${NC}                       → cek saldo rekening"
echo -e "    ${YELLOW}\"transfer 100rb\"${NC}               → transfer antar rekening"
echo -e "    ${YELLOW}\"undo\"${NC}                         → batalkan transaksi (30s)"
echo
echo -e "${CYAN}Update nanti:${NC}  ${YELLOW}hermes profile update kasio${NC}"
echo -e "${CYAN}Uninstall:${NC}     ${YELLOW}hermes profile remove kasio${NC}"

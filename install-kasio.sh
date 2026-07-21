#!/bin/bash
# ============================================================================
# KASIO Plugin + Skill Installer for Hermes Agent
# ============================================================================
# Usage:
#   ./install-kasio.sh
#   curl -fsSL https://raw.githubusercontent.com/arssnndr/kasio-distribution/main/install-kasio.sh | bash
#
# Behavior: Install plugin `kasio-notion` dan skill `kasio` ke profile Hermes
# yang SEDANG AKTIF. Tidak membuat profile baru. Aman dipakai berulang-ulang
# (idempotent) — kalau sudah terpasang, di-backup lalu di-update.
# ============================================================================

set -e

REPO="${KASIO_REPO:-github.com/arssnndr/kasio-distribution}"
BRANCH="${KASIO_BRANCH:-main}"
PLUGIN_NAME="kasio-notion"
SKILL_NAME="kasio"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; GRAY='\033[0;90m'; NC='\033[0m'

echo -e "${CYAN}╔════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  KASIO — Plugin & Skill Installer      ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════╝${NC}"
echo -e "  ${GRAY}Source : ${REPO} @ ${BRANCH}${NC}"
echo -e "  ${GRAY}Target : active Hermes profile (no new profile)${NC}"
echo

# 1. Verify Hermes
echo -e "${BLUE}[1/5]${NC} Checking Hermes installation..."
if ! command -v hermes &> /dev/null; then
    echo -e "${RED}✗ Hermes not found.${NC}"
    echo -e "  Install dulu:"
    echo -e "    ${YELLOW}curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Hermes found at $(command -v hermes)"

# Detect active profile (read-only, untuk info)
ACTIVE_PROFILE="$(hermes profile list 2>/dev/null | grep -E '^\*\s+' | head -1 | awk '{print $2}' | tr -d '*' || true)"
if [ -z "${ACTIVE_PROFILE:-}" ]; then ACTIVE_PROFILE="(unknown)"; fi
echo -e "${GREEN}✓${NC} Active profile: ${ACTIVE_PROFILE}"

# 2. Resolve Hermes dirs
echo -e "${BLUE}[2/5]${NC} Resolving target directories..."
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
if [ ! -d "${HERMES_HOME}" ]; then
    echo -e "${RED}✗ Hermes home not found: ${HERMES_HOME}${NC}"
    exit 1
fi
PLUGINS_DIR="${HERMES_HOME}/plugins"
SKILLS_DIR="${HERMES_HOME}/skills"
mkdir -p "${PLUGINS_DIR}" "${SKILLS_DIR}"
echo -e "${GREEN}✓${NC} Plugins dir: ${PLUGINS_DIR}"
echo -e "${GREEN}✓${NC} Skills dir : ${SKILLS_DIR}"

# 3. Download distribution zipball
echo -e "${BLUE}[3/5]${NC} Downloading distribution from ${REPO} ..."
TMP_ROOT="$(mktemp -d -t kasio-install-XXXXXX)"
ZIP_PATH="${TMP_ROOT}/kasio.zip"
ZIP_URL="https://codeload.github.com/${REPO#github.com/}/zip/refs/heads/${BRANCH}"
if command -v curl &> /dev/null; then
    curl -fsSL "${ZIP_URL}" -o "${ZIP_PATH}" || { echo -e "${RED}✗ curl failed${NC}"; exit 1; }
elif command -v wget &> /dev/null; then
    wget -q "${ZIP_URL}" -O "${ZIP_PATH}" || { echo -e "${RED}✗ wget failed${NC}"; exit 1; }
else
    echo -e "${RED}✗ Neither curl nor wget found${NC}"
    exit 1
fi
ZIP_SIZE=$(du -k "${ZIP_PATH}" | cut -f1)
echo -e "${GREEN}✓${NC} Downloaded ${ZIP_SIZE} KB"

# Extract zip
unzip -q "${ZIP_PATH}" -d "${TMP_ROOT}"
EXTRACTED_ROOT="$(find "${TMP_ROOT}" -maxdepth 1 -type d -name 'kasio-distribution-*' | head -1)"
if [ -z "${EXTRACTED_ROOT}" ]; then
    echo -e "${RED}✗ Extracted zip doesn't contain kasio-distribution-* folder${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Extracted to $(basename "${EXTRACTED_ROOT}")"

# 4. Install plugin
echo -e "${BLUE}[4/5]${NC} Installing plugin '${PLUGIN_NAME}' ..."
SRC_PLUGIN="${EXTRACTED_ROOT}/plugins/${PLUGIN_NAME}"
DST_PLUGIN="${PLUGINS_DIR}/${PLUGIN_NAME}"
if [ ! -d "${SRC_PLUGIN}" ]; then
    echo -e "${RED}✗ Plugin source not found: ${SRC_PLUGIN}${NC}"
    exit 1
fi

if [ -d "${DST_PLUGIN}" ]; then
    BACKUP="${DST_PLUGIN}.bak.$(date +%Y%m%d-%H%M%S)"
    mv "${DST_PLUGIN}" "${BACKUP}"
    echo -e "${YELLOW}!${NC} Existing plugin backed up to ${BACKUP}"
fi
cp -r "${SRC_PLUGIN}" "${DST_PLUGIN}"
# Bersihkan __pycache__ + .git dari extracted copy
rm -rf "${DST_PLUGIN}/__pycache__" "${DST_PLUGIN}/.git" 2>/dev/null || true

if hermes plugins list 2>&1 | grep -q "${PLUGIN_NAME}"; then
    echo -e "${GREEN}✓${NC} Plugin ${PLUGIN_NAME} installed and registered"
else
    echo -e "${YELLOW}!${NC} Plugin ${PLUGIN_NAME} copied but belum muncul di 'hermes plugins list'"
    echo -e "         Mungkin perlu restart Hermes session untuk auto-load."
fi

# 5. Install skill
echo -e "${BLUE}[5/5]${NC} Installing skill '${SKILL_NAME}' ..."
SRC_SKILL="${EXTRACTED_ROOT}/skills/${SKILL_NAME}"
DST_SKILL="${SKILLS_DIR}/${SKILL_NAME}"
if [ ! -d "${SRC_SKILL}" ]; then
    echo -e "${RED}✗ Skill source not found: ${SRC_SKILL}${NC}"
    exit 1
fi

if [ -d "${DST_SKILL}" ]; then
    BACKUP="${DST_SKILL}.bak.$(date +%Y%m%d-%H%M%S)"
    mv "${DST_SKILL}" "${BACKUP}"
    echo -e "${YELLOW}!${NC} Existing skill backed up to ${BACKUP}"
fi
cp -r "${SRC_SKILL}" "${DST_SKILL}"

if hermes skills list 2>&1 | grep -q "${SKILL_NAME}"; then
    echo -e "${GREEN}✓${NC} Skill ${SKILL_NAME} installed and registered"
else
    echo -e "${YELLOW}!${NC} Skill ${SKILL_NAME} copied but belum muncul di 'hermes skills list'"
fi

# Cleanup temp
rm -rf "${TMP_ROOT}"

# 6. Env setup (idempotent — skip kalau sudah ada)
ENV_FILE="${HERMES_HOME}/.env"
touch "${ENV_FILE}"

append_if_empty() {
    local key="$1"
    local desc="$2"
    if grep -q "^${key}=.\\+" "${ENV_FILE}" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} ${key} (already set, skipping)"
        return
    fi
    echo
    echo -e "${YELLOW}→ ${key}${NC}"
    echo "  ${desc}"
    read -p "  Value: " value
    echo "${key}=${value}" >> "${ENV_FILE}"
}

echo
echo -e "${BLUE}[env]${NC} Setup env (skip kalau sudah ada)..."
append_if_empty "NOTION_API_KEY" "Notion integration key (https://www.notion.so/my-integrations)"
append_if_empty "KASIO_TRANSACTIONS_DS_ID" "Notion data source ID untuk transactions DB"
append_if_empty "KASIO_ACCOUNTS_DS_ID" "Notion data source ID untuk accounts DB"

if ! grep -q "^MINIMAX_API_KEY=.\\+" "${ENV_FILE}" 2>/dev/null; then
    echo
    echo -e "${YELLOW}→ MINIMAX_API_KEY${NC} (optional — untuk vision reading foto struk)"
    echo "  Skip kalau tidak pakai vision (tekan Enter)"
    read -p "  Value (optional): " mm_key
    if [ -n "${mm_key}" ]; then
        echo "MINIMAX_API_KEY=${mm_key}" >> "${ENV_FILE}"
    fi
fi

echo
echo -e "${CYAN}╔════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  ${GREEN}Done!${CYAN}                                ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════╝${NC}"
echo
echo -e "Installed to active profile ${YELLOW}${ACTIVE_PROFILE}${NC} (no new profile created)."
echo
echo -e "${CYAN}Cara pakai:${NC}"
echo -e "  Chat bilang:"
echo -e "    ${YELLOW}\"catat makan siang 35rb\"${NC}        → wizard catat transaksi"
echo -e "    ${YELLOW}\"cek saldo\"${NC}                       → cek saldo rekening"
echo -e "    ${YELLOW}\"transfer 100rb dari cash ke seabank\"${NC} → transfer"
echo
echo -e "${CYAN}Update nanti:${NC}  ${YELLOW}./install-kasio.sh${NC}  (idempotent)"
echo -e "${CYAN}Uninstall:${NC}     ${YELLOW}rm -rf ${PLUGINS_DIR}/${PLUGIN_NAME} ${SKILLS_DIR}/${SKILL_NAME}${NC}"

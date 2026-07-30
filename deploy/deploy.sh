#!/bin/bash
# Supply Chain QA - ??????
# ??? Ubuntu 20.04+ / Debian 11+ / CentOS 8+
#
# ????:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# ?????:
#   1. ????? IP ??????? 4C8G ????? Milvus + embedding ???????
#   2. ???????? HTTPS?
#   3. DeepSeek API Key

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Supply Chain QA - ????${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# ---- 1. ?????? ----
echo -e "${YELLOW}[1/6] ??????...${NC}"

# ?? Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker ????????...${NC}"
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
fi

# ?? Docker Compose
if ! docker compose version &> /dev/null 2>&1; then
    echo -e "${RED}Docker Compose ????????...${NC}"
    apt-get update && apt-get install -y docker-compose-plugin 2>/dev/null || \
    curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose && \
    chmod +x /usr/local/bin/docker-compose
fi

echo -e "${GREEN}  Docker $(docker --version | cut -d' ' -f3 | cut -d',' -f1)${NC}"
echo -e "${GREEN}  Docker Compose $(docker compose version --short 2>/dev/null || docker-compose --version | cut -d' ' -f4 | cut -d',' -f1)${NC}"

# ---- 2. ???? ----
echo -e "${YELLOW}[2/6] ???????...${NC}"
TOTAL_MEM=$(free -m | awk '/^Mem:/{print $2}')
TOTAL_CPU=$(nproc)
echo -e "  CPU: ${TOTAL_CPU} ?"
echo -e "  ??: ${TOTAL_MEM} MB"

if [ "$TOTAL_MEM" -lt 4096 ]; then
    echo -e "${YELLOW}  ? ???? 4GB????? Reranker (RERANKER_ENABLED=false)${NC}"
fi

# ---- 3. ?????? ----
echo -e "${YELLOW}[3/6] ??????...${NC}"

if [ ! -f .env ]; then
    if [ -f deploy/.env.production ]; then
        cp deploy/.env.production .env
        echo -e "${GREEN}  ?????? .env ??${NC}"
    else
        echo -e "${RED}  ??? deploy/.env.production ??${NC}"
        exit 1
    fi
fi

# ?? API Key
if grep -q "sk-your-key-here" .env 2>/dev/null; then
    echo -e "${RED}  ? ???? .env ????? DEEPSEEK_API_KEY${NC}"
    echo -e "${YELLOW}  vim .env${NC}"
    read -p "  ????? .env? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# ---- 4. ???? ----
echo -e "${YELLOW}[4/6] ?? Docker ??????? 5-10 ???...${NC}"
docker compose -f docker-compose.prod.yml build --parallel

# ---- 5. ???? ----
echo -e "${YELLOW}[5/6] ??????...${NC}"
docker compose -f docker-compose.prod.yml up -d

# ---- 6. ?????? ----
echo -e "${YELLOW}[6/6] ???????? 60 ??...${NC}"

# ??????
echo -n "  ??????"
for i in $(seq 1 60); do
    if curl -s http://localhost:8001/health > /dev/null 2>&1; then
        echo -e " ${GREEN}?${NC}"
        break
    fi
    echo -n "."
    sleep 2
done

# ????
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}  ?????${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# ???? IP
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ip.sb 2>/dev/null || echo "YOUR_SERVER_IP")

echo -e "  ????: ${GREEN}http://${PUBLIC_IP}${NC}"
echo -e "  API ??: ${GREEN}http://${PUBLIC_IP}/api/docs${NC}"
echo -e "  ????: ${GREEN}http://${PUBLIC_IP}/health${NC}"
echo ""
echo -e "  ????: ${YELLOW}admin / admin123${NC}"
echo ""
echo -e "  ????: docker compose -f docker-compose.prod.yml logs -f"
echo -e "  ????: docker compose -f docker-compose.prod.yml down"
echo ""

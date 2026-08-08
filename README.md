# ClassHub OSS

> 自架版的班級與社團聯絡工具。自己架在自己的伺服器上，資料完全自己掌控。

ClassHub OSS 是 ClassHub 的開源自架版本。一個輕量、快速的一對多資訊分享工具，協助老師、教練與社團把公告、提醒、圖片一次發給所有家長，不再被群組訊息淹沒。

它是 RWD 網頁服務，可在手機、平板與電腦瀏覽器中使用，不需要安裝 App。不需要 AI 供應商，不產生 token 成本，也不綁定特定通訊平台。

跟 hosted 版（classhub）的差別：OSS 版是你自己架、自己管理。沒有人數審核、沒有服務時間限制，但所有維運、備份、安全更新都由你負責。

---

## 目錄

- [快速開始（一般使用者）](#快速開始一般使用者)
- [技術人員安裝指南](#技術人員安裝指南)
- [Google Drive 圖片儲存設定](#google-drive-圖片儲存設定)
- [用 Cloudflare Tunnel 暴露到網際網路](#用-cloudflare-tunnel-暴露到網際網路)
- [日常維運](#日常維運)
- [系統架構](#系統架構)
- [授權](#授權)

---

## 快速開始（一般使用者）

ClassHub OSS 分成兩個部分：後端（API，處理資料）和前端（網頁畫面）。你需要把兩個都跑起來。

### 你需要準備什麼

| 項目 | 說明 |
|------|------|
| 一台電腦或伺服器 | Linux、Mac、Windows 都可以。建議 Linux |
| Python 3.11 以上 | 後端程式需要的執行環境。[下載](https://www.python.org/downloads/) |
| Node.js 20 以上 | 前端程式需要的建構工具。[下載](https://nodejs.org/) |

確認安裝是否成功：

```bash
python3 --version    # 要看到 3.11 以上
node --version       # 要看到 v20 以上
```

### 第一步：下載程式碼

```bash
git clone https://github.com/apollo-muvi/ossclasshub.git
cd ossclasshub
```

如果沒有 git，先安裝：`sudo apt install git -y`

### 第二步：設定環境變數

提供兩種方式，選一種即可。

#### 方式 A：自動產生（推薦，適合不熟悉的使用者）

repo 附了一支設定腳本，會自動複製 `.env.example` 並產生安全的加密金鑰：

```bash
cd ossclasshub
python3 setup-env.py
```

腳本會：
- 把 `.env.example` 複製到 `api/.env`
- 自動產生隨機的 `CLASSHUB_SECRET`（64 字 hex）
- 顯示下一步該做什麼

> 腳本只是幫你省去手動複製和產生金鑰的步驟，不會安裝任何東西。在 Linux、macOS、Windows 上都能跑。
>
> 如果 `api/.env` 已經存在，腳本會問你要不要覆蓋。加 `--force` 參數可以直接覆蓋不詢問。

#### 方式 B：手動操作

```bash
cd ossclasshub
cp .env.example api/.env
```

用文字編輯器打開 `api/.env`（例如 `nano api/.env`），把 `CLASSHUB_SECRET` 改成你自己的值。這是系統的加密金鑰，**不可以保留預設值 `change-me`**，否則任何人都能偽造登入 token。

```bash
CLASSHUB_SECRET=a3f8e2b1c9d7...    # 改成一段隨機字串，至少 32 個字
```

快速產生隨機金鑰：

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

> 其他欄位保留預設值即可。之後要用 Google Drive 存圖片或調整資料庫路徑時再回來改。

### 第三步：啟動 ClassHub

repo 附有一鍵部署腳本，會同時啟動後端和前端，不需要開兩個終端機視窗：

```bash
cd ossclasshub
./deploy-classhub.sh start
```

第一次執行時腳本會自動：
- 建立 Python 虛擬環境並安裝後端 dependencies
- 安裝前端 dependencies（npm install）
- 啟動後端（port 8100）和前端（port 5174）

看到類似以下輸出就代表啟動成功：

```
Starting ClassHub...
  Creating Python venv...
  Installing frontend dependencies...
  Starting API on :8100 (DB: /tmp/classhub-oss.db)
  Starting Frontend on :5174 (proxy → :8100)
ClassHub OSS status:
  API       : running (pid 12345) → http://localhost:8100
  Frontend  : running (pid 12346) → http://localhost:5174
```

腳本常用指令：

```bash
./deploy-classhub.sh stop      # 停止
./deploy-classhub.sh restart   # 重新啟動
./deploy-classhub.sh status    # 查看狀態
```

> **Mac / Windows 使用者**：deploy-classhub.sh 是 bash 腳本，在 Windows 上需要用 WSL（Windows Subsystem for Linux）或 Git Bash 執行。如果不想用 bash，可以改用手動啟動（見下方）。

### 第四步：首次設定與登入

打開瀏覽器，前往 `http://localhost:5174/`

第一次使用時，系統會要求你建立管理者帳號：
- 輸入使用者名稱（至少 2 個字）
- 輸入密碼（至少 8 個字）
- 送出後就建立完成

之後就可以用這組帳號密碼登入，開始建立班級、匯入學生名單、發佈聯絡簿。

> 家長不需要帳號。老師建立學生資料後，系統會產生專屬的家長分享連結和 QR Code，家長打開連結就能看到今日聯絡簿。

---

## 技術人員安裝指南

### 架構概覽

| 元件 | 技術 | 預設 Port |
|------|------|-----------|
| 後端 API | FastAPI + SQLite | 8100 |
| 前端 | React + Vite (dev) | 5174 |
| 資料庫 | SQLite（檔案型，無需額外安裝） | — |

前端 Vite dev server 內建 reverse proxy，會自動把 `/api/` 和 `/health` 請求轉發到後端。開發環境只需指定 `VITE_API_PROXY_TARGET`。

### 一鍵部署腳本

快速開始已介紹過 `deploy-classhub.sh`，這裡補充技術細節。

腳本會在背景啟動後端和前端，用 PID 檔管理行程。可控參數（用環境變數覆蓋）：

```bash
# 啟動（後端 + 前端）
./deploy-classhub.sh start

# 停止
./deploy-classhub.sh stop

# 重新啟動
./deploy-classhub.sh restart

# 重建前端
./deploy-classhub.sh build

# 查看狀態
./deploy-classhub.sh status

# 看日誌
./deploy-classhub.sh logs api
./deploy-classhub.sh logs web
```

腳本可控參數（用環境變數覆蓋）：

```bash
PORT_API=8100          # 後端 port
PORT_WEB=5174          # 前端 port
DB_PATH=/tmp/classhub-oss.db   # 資料庫檔案路徑
REPO_DIR=/home/you/ossclasshub  # repo 位置
PID_DIR=/tmp/classhub-pids      # PID 存放目錄
```

正式環境建議用 systemd 管理，不要用 nohup + dev server。下方有範例。

### 手動安裝（不用腳本）

適合不用 Linux / bash 的使用者，或想了解每步做什麼的人。需要開兩個終端機視窗。

安裝 dependencies（第一次才需要）：

```bash
# 視窗 1：後端
cd ossclasshub/api
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 視窗 2：前端
cd ossclasshub/web
npm install
```

啟動（每次使用都要執行）：

```bash
# 視窗 1：啟動後端
cd ossclasshub/api
. .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8100

# 視窗 2：啟動前端
cd ossclasshub/web
npm run dev -- --host 0.0.0.0 --port 5174
```

兩個視窗都要保持開啟。要關掉服務時，在各自的視窗按 `Ctrl + C`。

### 環境變數

repo 根目錄有 `.env.example`，複製為 `.env` 後放在 `api/` 目錄下（pydantic-settings 會自動讀取）：

```bash
CLASSHUB_APP_NAME=ClassHub          # 顯示名稱
CLASSHUB_SECRET=change-me           # JWT 加密金鑰，正式環境一定要改
CLASSHUB_DB=classhub.db             # SQLite 資料庫檔案路徑
CLASSHUB_UPLOAD_DIR=uploads         # 圖片上傳目錄
CLASSHUB_IMAGE_STORAGE_PROVIDER=local  # 圖片儲存方式（local 或 google_drive）
```

> `CLASSHUB_SECRET` 是 JWT 簽名金鑰。正式環境一定要改成一段隨機字串（至少 32 字）。如果這個值被別人知道，別人可以偽造登入 token。

### Production 前端（靜態建構）

開發環境用 Vite dev server。正式環境建議建構成靜態檔案，用 nginx 或其他網頁伺服器提供：

```bash
cd web
VITE_API_PROXY_TARGET=http://localhost:8100 npm run build
# 產出在 web/dist/，用 nginx 或 caddy 提供靜態服務
```

nginx 設定參考（把 /api/ 轉發到後端）：

```nginx
server {
    listen 80;
    server_name classhub.example.com;

    root /path/to/web/dist;
    index index.html;
    client_max_body_size 12m;

    location /api/ {
        proxy_pass http://127.0.0.1:8100/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

### systemd 服務（後端）

```ini
# /etc/systemd/system/classhub-api.service
[Unit]
Description=ClassHub OSS API
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/ossclasshub/api
EnvironmentFile=/opt/ossclasshub/api/.env
ExecStart=/opt/ossclasshub/api/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8100
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable classhub-api
sudo systemctl start classhub-api
```

### 健康檢查

```bash
curl http://localhost:8100/api/v1/health
# {"status":"ok","setup_completed":true,"schema_version":"1"}
```

### 執行測試

```bash
cd api && python -m pytest -q
cd web && npm run build
```

---

## Google Drive 圖片儲存設定

預設使用本地檔案系統儲存老師上傳的圖片。如果想改用 Google Drive（免費 15GB 額度），需要完成以下設定。

### 事前準備

1. 準備一個 Google Cloud Project，用來建立 ClassHub 的 OAuth 2.0 Web Client。完成系統設定後，由實際使用 Google Drive 的 ClassHub 管理者透過「Connect Google Drive」授權自己的 Google 帳號；不需要建立 Google Service Account。
2. 建立 Google OAuth 2.0 憑證

### 第一步：建立 Google OAuth 憑證

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 建立新專案（或選擇現有專案）
3. 左側選「API 和服務」→「啟用 API 和服務」
4. 搜尋並啟用 **Google Drive API**
5. 左側選「憑證」→「建立憑證」→「OAuth 用戶端 ID」
6. 應用程式類型選「網頁應用程式」
7. 在「授權的重新導向 URI」填入：

```
http://你的伺服器網址/api/v1/integrations/google-drive/callback
```

> 本地測試：`http://localhost:8100/api/v1/integrations/google-drive/callback`
> 正式環境（搭配 Cloudflare Tunnel）：`https://classhub.你的網域/api/v1/integrations/google-drive/callback`
>
> URI 必須完全一致（協定、主機、Port、路徑、結尾斜線都不能差），否則 Google 會回傳 `redirect_uri_mismatch` 錯誤。

8. 建立後記下 **Client ID** 和 **Client Secret**

9. 如果畫面顯示「未發布應用程式」，需到「OAuth 同意畫面」將發布狀態改為「正式」，或將測試使用者加入。

### 第二步：產生加密金鑰

OAuth token 存進資料庫前會加密。產生一把 Fernet 金鑰：

```bash
cd api
. .venv/bin/activate
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

把輸出的那串字（例如 `a8Kx...3fY=`）記下來。

### 第三步：設定環境變數

在 `.env` 加入以下變數：

```bash
CLASSHUB_IMAGE_STORAGE_PROVIDER=local
CLASSHUB_GOOGLE_CLIENT_ID=你的OAuth_Client_ID
CLASSHUB_GOOGLE_CLIENT_SECRET=你的OAuth_Client_Secret
CLASSHUB_GOOGLE_REDIRECT_URI=http://localhost:8100/api/v1/integrations/google-drive/callback
CLASSHUB_TOKEN_ENCRYPTION_KEY=你的Fernet金鑰
```

> 注意：這一步先保持 `IMAGE_STORAGE_PROVIDER=local`。等 OAuth 連線成功後，才透過 API 切換成 `google_drive`。

重新啟動後端讓設定生效。

### 第四步：授權連線

先登入取得 admin token（假設帳號是 admin）：

```bash
curl -s http://localhost:8100/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"你的密碼"}' | jq .
# 記下 access_token
```

取得 Google 授權連結：

```bash
TOKEN="剛才拿到的access_token"

curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8100/api/v1/integrations/google-drive/connect?as_json=true" | jq .
# {"url":"https://accounts.google.com/o/oauth2/auth?..."}
```

在瀏覽器打開那個 `url`，完成 Google 授權。授權成功後會回到 callback URL，看到 `{"ok":true,"status":"connected"}` 就代表連線成功。

確認連線狀態：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8100/api/v1/integrations/google-drive/status | jq .
# "status": "connected" 代表成功
```

### 第五步：切換圖片儲存到 Google Drive

圖片儲存位置由 `CLASSHUB_IMAGE_STORAGE_PROVIDER` 控制，但可以透過兩種方式設定：

| 方式 | 寫在哪 | 優先順序 |
|------|--------|---------|
| API 切換 | 資料庫 settings 表 | 高（有設定就用這個） |
| 環境變數 | `.env` 的 `CLASSHUB_IMAGE_STORAGE_PROVIDER` | 低（API 沒設過才用這個） |

也就是說，系統啟動時先讀環境變數當預設值，但如果曾經透過 API 切換過，資料庫裡的值會蓋過環境變數。

#### 方法 A：直接改環境變數（最簡單）

在 `.env` 改一行就好：

```bash
CLASSHUB_IMAGE_STORAGE_PROVIDER=google_drive
```

重新啟動後端生效。這種方式適合首次安裝時直接決定用 Google Drive。

#### 方法 B：透過 API 切換（不用重啟服務）

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"google_drive"}' \
  http://localhost:8100/api/v1/storage/settings | jq .
```

這會把設定寫進資料庫，即時生效不用重啟。之後就算重啟服務，資料庫裡的值仍然蓋過環境變數。

> 前提：用 API 切換到 google_drive 之前，必須先完成第四步的 OAuth 授權連線，否則系統會拒絕（回傳 409 錯誤）。

切換後，新上傳的圖片會存到你的 Google Drive 的 `ClassHub/Images` 資料夾。之前用 local 存的圖片不受影響，仍從本地讀取。

### 切回本地儲存

跟切換到 Google Drive 一樣，兩種方式：

```bash
# 方法 A：改 .env（需重啟）
CLASSHUB_IMAGE_STORAGE_PROVIDER=local

# 方法 B：API 切換（即時生效，會蓋過環境變數）
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"local"}' \
  http://localhost:8100/api/v1/storage/settings | jq .
```

### 中斷 Google Drive 連線

```bash
# revoke=false 只移除本地 OAuth 憑證，不撤銷 Google 端授權
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8100/api/v1/integrations/google-drive/disconnect?revoke=false" | jq .

# revoke=true 同時撤銷 Google 端的授權
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8100/api/v1/integrations/google-drive/disconnect?revoke=true" | jq .
```

中斷後，未來上傳會自動回到本地儲存。已存在 Google Drive 的檔案不會被刪除，也不會被移到本地。

---

## 用 Cloudflare Tunnel 暴露到網際網路

ClassHub OSS 本身不包含 Cloudflare Tunnel 設定。如果你想把自架的 ClassHub 開放給外部網路使用，以下是完整步驟。

### 為什麼用 Cloudflare Tunnel

| 方式 | 需要公開 IP | 需要開防火牆 Port | HTTPS | 成本 |
|------|------------|-------------------|-------|------|
| 直接開 Port | 要 | 要 | 要自己申請憑證 | 免費 |
| Cloudflare Tunnel | 不用 | 不用 | 自動 | 免費 |

Cloudflare Tunnel 不需要在伺服器開任何對外 Port，所有流量都從 Cloudflare 出站連線出去。安全性更高，設定也更簡單。

### 事前準備

1. 一個網域名稱（例如 `example.com`），並且已經把 DNS 託管到 Cloudflare
2. 如果還沒把 DNS 移到 Cloudflare：登入 [Cloudflare Dashboard](https://dash.cloudflare.com/) → 新增 Site → 依照指示把 nameserver 改成 Cloudflare 的

### 第一步：安裝 cloudflared

```bash
# Debian / Ubuntu
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update
sudo apt install cloudflared -y

# 確認安裝
cloudflared --version
```

### 第二步：登入 Cloudflare

```bash
cloudflared tunnel login
```

這會顯示一個網址。在瀏覽器打開，選擇你的網域授權。授權成功後，憑證會存到 `~/.cloudflared/cert.pem`。

### 第三步：建立 Tunnel

```bash
cloudflared tunnel create classhub
```

這會建立一條名為 `classhub` 的 Tunnel，並產生一個 UUID。記下這個 UUID（之後的設定會用到）。

### 第四步：設定 DNS

讓你的子網域（例如 `classhub.example.com`）指向這條 Tunnel：

```bash
cloudflared tunnel route dns classhub classhub.example.com
```

這會自動在 Cloudflare 建一筆 CNAME 指到 Tunnel。不需要手動去 Cloudflare Dashboard 加 DNS。

### 第五步：建立設定檔

建立 `~/.cloudflared/config.yml`：

```yaml
tunnel: 你的Tunnel-UUID
credentials-file: /home/你的帳號/.cloudflared/你的Tunnel-UUID.json

ingress:
  - hostname: classhub.example.com
    service: http://localhost:5174
  - service: http_status:404
```

> 上面的 `service` 指向 `localhost:5174`（Vite dev server）。如果你用 nginx 提供靜態建構的前端，就改成 `http://localhost:80` 或你設定的 nginx port。
>
> 前端的 Vite proxy 會自動把 `/api/` 轉發到後端 8100，所以 Tunnel 只需要指向前端。

### 第六步：啟動 Tunnel

先測試：

```bash
cloudflared tunnel run classhub
```

看到 `Registered tunnel connection` 就代表成功。打開瀏覽器測試 `https://classhub.example.com` 能不能連到 ClassHub。

### 第七步：設為常駐服務

確認沒問題後，用 systemd 讓 Tunnel 開機自動啟動：

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

> `cloudflared service install` 會自動讀取 `~/.cloudflared/config.yml`，把它複製到系統目錄。

### 如果用了 Google Drive

設定 Google Drive 時，OAuth Redirect URI 要改成你的正式網址：

```bash
CLASSHUB_GOOGLE_REDIRECT_URI=https://classhub.example.com/api/v1/integrations/google-drive/callback
```

Google Cloud Console 裡的「授權的重新導向 URI」也要同步改成這個網址。

### Tunnel 維運常用指令

```bash
# 查看 Tunnel 狀態
cloudflared tunnel info classhub

# 停止服務
sudo systemctl stop cloudflared

# 重新啟動（改完 config.yml 後執行）
sudo systemctl restart cloudflared

# 查看日誌
sudo journalctl -u cloudflared -f --no-pager

# 刪除 Tunnel（需先停止服務）
cloudflared tunnel delete classhub
```

---

## 日常維運

### 備份

SQLite 資料庫是一個檔案，直接複製就是備份：

```bash
# 安全備份（不會鎖住寫入）
sqlite3 /path/to/classhub.db ".backup /path/to/backup/classhub-$(date +%Y%m%d).db"
```

建議設定 cron 每天自動備份：

```bash
# crontab -e
0 3 * * * sqlite3 /opt/ossclasshub/api/classhub.db ".backup /backup/classhub-$(date +\%Y\%m\%d).db"
```

如果用了 Google Drive 存圖片，圖片的備份在 Google Drive 端。本地 `uploads/` 目錄也建議定期備份（存放 OAuth 前上傳的舊圖片）。

### 更新

```bash
cd ossclasshub
git pull
cd api && . .venv/bin/activate && pip install -e ".[dev]"
cd ../web && npm install
# 重建前端（如果用靜態建構）
npm run build
# 重啟服務
sudo systemctl restart classhub-api
```

### 查看日誌

```bash
# 後端（systemd 管理）
sudo journalctl -u classhub-api -f --no-pager

# 後端（deploy-classhub.sh 管理）
tail -f /tmp/classhub-pids/api.log

# 前端（deploy-classhub.sh 管理）
tail -f /tmp/classhub-pids/web.log

# Cloudflare Tunnel
sudo journalctl -u cloudflared -f --no-pager
```

---

## 系統架構

```
使用者瀏覽器
    │
    ↓ (https://classhub.example.com)
    │
 Cloudflare Tunnel (自動 HTTPS)
    │
    ↓ (http://localhost:5174 或 :80)
    │
 Frontend (React + Vite 或 nginx 靜態)
    │
    ├── /api/*  →  proxy  →  Backend (FastAPI :8100)
    │                              │
    │                              ├── SQLite (classhub.db)
    │                              ├── Local uploads/ (本地圖片)
    │                              └── Google Drive API (OAuth 圖片儲存)
    │
    └── /*       →  靜態 HTML/JS
```

### 技術規格

- 後端：Python 3.11+ / FastAPI / SQLite
- 前端：React / Vite
- 圖片儲存：本地檔案系統（預設）/ Google Drive（OAuth）
- 外部暴露：Cloudflare Tunnel（推薦）/ nginx 反向代理

### 不包含

- LLM / RAG / Embeddings / Vector DB
- Token 計費
- AI Workers
- 多租戶隔離（OSS 版為單一實例）

---

## 授權

MIT License

const { app, BrowserWindow, ipcMain, screen, Tray, Menu } = require('electron'); // 👈 [新增] Tray, Menu
const path = require('path');
const fs = require('fs'); // 👈 [新增] 用於讀寫 config.json

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
let win;
let settingsWin; // 👈 [新增] 設定視窗的變數
let tray = null; // 👈 [新增] 托盤的變數


const configPath = path.join(__dirname, 'pet_backend/config.json');

function createWindow() {
  // 1. 先讀取設定檔抓取縮放比例
  let scale = 1.0;
  try {
    const configData = fs.readFileSync(configPath, 'utf-8');
    const config = JSON.parse(configData);
    if (config.model_scale) scale = config.model_scale;
  } catch (error) {
    console.error("讀取設定檔失敗，使用預設大小", error);
  }

  // 2. 動態計算寬高 (基準寬 400, 高 600)
  const baseWidth = 400;
  const baseHeight = 600;

  win = new BrowserWindow({
    width: Math.round(baseWidth * scale),
    height: Math.round(baseHeight * scale),
    icon: path.join(__dirname, 'assets/icon.ico'),
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    backgroundColor: '#00000000',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      webSecurity: false 
    }
  });

  win.loadFile('index.html');
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  try {
    const configData = fs.readFileSync(configPath, 'utf-8');
    const config = JSON.parse(configData);
    if (config.show_terminal) {
      // 以獨立視窗 (detach) 模式開啟前端終端機，避免破壞桌寵透明佈局
      win.webContents.openDevTools({ mode: 'detach' }); 
    }
  } catch (error) {
    console.error("讀取設定檔以開啟終端機時發生錯誤:", error);
  }
  setInterval(() => {
    if (win && !win.isDestroyed()) {
      const point = screen.getCursorScreenPoint();
      const bounds = win.getBounds();
      const relativeX = point.x - bounds.x;
      const relativeY = point.y - bounds.y;
      win.webContents.send('global-mouse-move', { x: relativeX, y: relativeY });
    }
  }, 30);
}

// 👇 [新增] 建立設定視窗的函式
function createSettingsWindow() {
  settingsWin = new BrowserWindow({
    width: 600,
    height: 400,
    icon: path.join(__dirname, 'assets/icon.ico'),
    show: false, // 初始狀態設為隱藏
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    }
  });

  settingsWin.loadFile('settings.html');

  // 攔截關閉事件，改為隱藏，這樣下次打開才會快，且不會報錯
  settingsWin.on('close', (e) => {
    e.preventDefault();
    settingsWin.hide();
  });
}

function createTray() {
  tray = new Tray(path.join(__dirname, 'assets/icon.ico'));

  // 💡 讀取目前的 config 內容來動態顯示 Base URL
  let currentBaseUrl = "未設定";
  try {
    const configData = fs.readFileSync(configPath, 'utf-8');
    const config = JSON.parse(configData);
    if (config.base_url) currentBaseUrl = config.base_url;
  } catch (e) {
    console.error("托盤讀取 config 失敗", e);
  }

  const contextMenu = Menu.buildFromTemplate([
    { label: `API 網址: ${currentBaseUrl}`, enabled: false }, // 顯示用（不可點擊）
    { type: 'separator' },
    { label: '顯示設定', click: () => settingsWin.show() },
    { label: '結束程式', click: () => { 
      app.isQuiting = true; 
      app.quit(); 
    }}
  ]);

  tray.setToolTip('Murasame 桌寵設定');
  tray.setContextMenu(contextMenu);

  tray.on('click', () => {
    if (settingsWin.isVisible()) {
      settingsWin.hide();
    } else {
      settingsWin.show();
    }
  });
}

// 啟動時一併載入
app.whenReady().then(() => {
  createWindow();
  createSettingsWindow(); // 👈 [新增]
  createTray();           // 👈 [新增]
  // 👇 [新增] 檢查啟動參數，如果有 --show-settings 就自動顯示設定視窗
  if (process.argv.includes('--show-settings')) {
    settingsWin.show();
  }
});

// 修正：當真正退出時，解除 settingsWin 的攔截
app.on('before-quit', () => {
  if (settingsWin) settingsWin.destroy();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// ==========================================
// --- [新增] IPC：讀寫 config.json ---
// ==========================================

// 讓渲染進程索取 config 資料
ipcMain.handle('get-config', async () => {
  try {
    const data = fs.readFileSync(configPath, 'utf-8');
    return JSON.parse(data);
  } catch (error) {
    console.error('讀取 config 失敗:', error);
    return { error: '讀取設定檔失敗' };
  }
});

// 讓渲染進程儲存修改後的 config 資料
ipcMain.handle('save-config', async (event, newConfig) => {
  try {
    fs.writeFileSync(configPath, JSON.stringify(newConfig, null, 4), 'utf-8');
    return { success: true };
  } catch (error) {
    console.error('寫入 config 失敗:', error);
    return { success: false, error: error.message };
  }
});
// ==========================================
// --- [新增] IPC：熱修改套用 UI 設定 ---
// ==========================================
ipcMain.on('apply-ui-settings', () => {
  try {
    const configData = fs.readFileSync(configPath, 'utf-8');
    const config = JSON.parse(configData);
    const scale = config.model_scale || 1.0;
    
    if (win && !win.isDestroyed()) {
      const baseWidth = 400;
      const baseHeight = 600;
      const newWidth = Math.round(baseWidth * scale);
      const newHeight = Math.round(baseHeight * scale);
      
      // ✅ 修正：只有當視窗大小真的需要改變時，才執行 setContentSize
      const currentSize = win.getContentSize();
      if (currentSize[0] !== newWidth || currentSize[1] !== newHeight) {
        win.setContentSize(newWidth, newHeight);
      }
      
      win.webContents.send('scale-model', scale);
    }
  } catch (error) {
    console.error('套用 UI 設定失敗:', error);
  }
});
// ==========================================
// --- [新增] IPC：完全退出桌寵 ---
// ==========================================
ipcMain.on('quit-app', () => {
  app.isQuiting = true;
  app.quit(); // 關閉前端，這會觸發剛剛 launch.js 裡的連動關閉機制
});
// ==========================================
// --- 強制鎖定長寬的右鍵拖曳邏輯 (保留你原本的程式碼) ---
// ==========================================
let dragInterval = null;
let startMouse = { x: 0, y: 0 };
let startWindowPos = { x: 0, y: 0 };

ipcMain.on('start-right-drag', () => {
  if (!win) return;
  
  startMouse = screen.getCursorScreenPoint();
  // 1. 改用 getPosition，只取純座標，不取會誤差的 Bounds
  const [startX, startY] = win.getPosition(); 
  startWindowPos = { x: startX, y: startY };
  
  // 2. 每次拖曳前，抓取絕對精準的縮放比例，計算出「不可變動的真實寬高」
  let scale = 1.0;
  try {
    const configData = fs.readFileSync(configPath, 'utf-8');
    const config = JSON.parse(configData);
    if (config.model_scale) scale = config.model_scale;
  } catch (error) {}

  const exactWidth = Math.round(400 * scale);
  const exactHeight = Math.round(600 * scale); // 確保對齊 600 的高度標準

  if (dragInterval) clearInterval(dragInterval);
  
  dragInterval = setInterval(() => {
    if (!win || win.isDestroyed()) {
      clearInterval(dragInterval);
      return;
    }
    
    const currentMouse = screen.getCursorScreenPoint();
    const deltaX = currentMouse.x - startMouse.x;
    const deltaY = currentMouse.y - startMouse.y;
    
    // 3. 每次移動都「強制」把正確的長寬塞回去，徹底抹殺自動擴張 Bug
    win.setBounds({
      x: Math.round(startWindowPos.x + deltaX),
      y: Math.round(startWindowPos.y + deltaY),
      width: exactWidth,
      height: exactHeight
    });
  }, 15); 
});

ipcMain.on('stop-right-drag', () => {
  if (dragInterval) clearInterval(dragInterval);
});
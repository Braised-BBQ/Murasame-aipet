const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const BACKEND_DIR = path.join(__dirname, 'pet_backend');
const FRONTEND_DIR = __dirname; 

let backendProcess = null;
let frontendProcess = null;
let isBackendRestarting = false;

// 檢查後端是否已經啟動成功 (透過 ping 本地 8000 埠)
function checkBackendReady(callback) {
  const req = http.get('http://localhost:8000/docs', (res) => {
    // 只要能連上 FastAPI 的文件頁面或任何回應，代表後端已經啟動完畢
    if (res.statusCode < 500) {
      callback();
    } else {
      setTimeout(() => checkBackendReady(callback), 1000);
    }
  });

  req.on('error', () => {
    // 連線失敗（代表後端還沒開好），每秒重試一次
    setTimeout(() => checkBackendReady(callback), 1000);
  });
}

// 1. 啟動 Python 中轉層
function startBackend() {
  console.log('----------------------------------------');
  console.log('[Launcher] 正在啟動 Python 中轉層...');
  
  backendProcess = spawn('python', ['main.py'], {
    cwd: BACKEND_DIR,
    shell: true,
    stdio: 'inherit'
  });

  backendProcess.on('exit', (code) => {
    console.log(`\n[Launcher] 警告：Python 中轉層已關閉 (Code: ${code})`);
    isBackendRestarting = true;

    stopFrontend(() => {
      console.log('[Launcher] 正在等待後端完全關閉並準備重啟...');
      setTimeout(() => {
        startBackend();
        
        // 開始探測後端是否開機完成，完成後才啟動前端
        console.log('[Launcher] 正在等待後端初始化完成...');
        checkBackendReady(() => {
          console.log('[Launcher] ✅ 後端已完全就緒！準備啟動前端...');
          setTimeout(() => {
            isBackendRestarting = false;
            startFrontend();
          }, 1000); // 確保後端穩定後啟動前端
        });
      }, 2000);
    });
  });
}

// 2. 啟動 Electron 前端
function startFrontend() {
  if (isBackendRestarting) return;
  console.log('[Launcher] 正在啟動 Electron 前端 (npm start)...');
  
  frontendProcess = spawn('npm', ['start'], {
    cwd: FRONTEND_DIR,
    shell: true,
    stdio: 'inherit'
  });

  frontendProcess.on('exit', (code) => {
    if (!isBackendRestarting) {
      console.log(`[Launcher] 前端已關閉 (Code: ${code})`);
    }
  });
}

// 關閉前端輔助函式
function stopFrontend(callback) {
  if (frontendProcess) {
    console.log('[Launcher] 正在關閉 Electron 前端...');
    spawn('taskkill', ['/pid', frontendProcess.pid, '/f', '/t'], { shell: true })
      .on('exit', () => {
        frontendProcess = null;
        if (callback) callback();
      });
  } else {
    if (callback) callback();
  }
}

// === 初始啟動流程 ===
startBackend();

console.log('[Launcher] 正在等待後端初始化完成（首度開機）...');
checkBackendReady(() => {
  console.log('[Launcher] ✅ 後端已完全就緒！正在啟動 Electron 前端...');
  startFrontend();
});